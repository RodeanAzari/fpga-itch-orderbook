// replay: sends an ITCH 5.0 file as a MoldUDP64 feed over UDP (docs/buildplan.md §7).
//
// Messages are batched greedily into packets of at most --max-payload bytes (default 1,472),
// numbered from --first-seq, and followed by one end-of-session packet. The packets do not
// depend on the pacing mode, so the expected decode of a run is determined by the input
// file, --skip/--count, --session, --first-seq, --max-payload and the fault flags.
//
// Pacing busy-waits on an absolute CLOCK_MONOTONIC schedule:
//   flat out (default)   send as fast as the socket accepts
//   --pps N              packet k is due at t0 + k / N
//   --realtime [--speed X]  a packet is due at t0 + (its first message's ITCH timestamp -
//                        the first packet's) / X. Late packets go out at once, so bursts
//                        follow; the summary reports how far behind sends fell (max_lag_ms).
// A send that fails with ENOBUFS/EAGAIN/EINTR is retried (send_retries); any other error
// aborts. --out FILE writes the packets as u16 BE length + bytes instead of sending (the
// same framing as an ITCH file, so model.itch.iter_raw reads it back).
//
// Output: a progress line per second on stderr; a key=value summary on stdout for the
// results sidecar. Ctrl-C stops cleanly and still prints the summary (no end of session).

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <exception>
#include <span>
#include <string>
#include <vector>

#include "mold.hpp"

namespace {

// Bytes on the wire per datagram beyond the payload: preamble+SFD 8, Ethernet header 14,
// IPv4 20, UDP 8, FCS 4, inter-frame gap 12. Payloads under 18 bytes pad to a 64-byte frame.
constexpr uint64_t kWireOverhead = 8 + 14 + 20 + 8 + 4 + 12;
constexpr uint64_t kMinFramePayload = 18;  // 64 - 14 - 20 - 8 - 4

volatile std::sig_atomic_t g_stop = 0;
void on_signal(int) { g_stop = 1; }

int64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1'000'000'000 + ts.tv_nsec;
}

uint64_t wire_bytes(size_t payload) {
    return std::max<uint64_t>(payload, kMinFramePayload) + kWireOverhead;
}

[[noreturn]] void usage(const char* argv0) {
    std::fprintf(stderr,
                 "usage: %s --in FILE (--dst IP --port N [--if-ip IP] | --out FILE)\n"
                 "          [--pps N | --realtime [--speed X]] [--skip N] [--count N]\n"
                 "          [--session NAME] [--first-seq N] [--max-payload BYTES]\n"
                 "          [--drop-every N] [--dup-every N] [--swap-every N]\n"
                 "  --in          length-prefixed ITCH 5.0 file, gzip or plain\n"
                 "  --dst/--port  destination (multicast group or unicast)\n"
                 "  --if-ip       local interface address for multicast (IP_MULTICAST_IF)\n"
                 "  --out         write packets to FILE (u16 BE length + bytes) instead\n"
                 "  --skip/--count  messages to skip, then at most this many (0 = all)\n"
                 "  --session     up to 10 bytes, space-padded (default REPLAY0001)\n"
                 "  --first-seq   sequence number of the first message sent (default 1)\n"
                 "  --max-payload UDP payload limit, %zu..%zu (default %zu)\n"
                 "  --drop/dup/swap-every N  fault on every Nth built packet (0 = off)\n",
                 argv0, mold::kHeaderLen + mold::kLenPrefix + 1, mold::kMaxUdpPayload,
                 mold::kMaxUdpPayload);
    std::exit(2);
}

struct Args {
    std::string in, dst, if_ip, out;
    std::string session = "REPLAY0001";
    int port = -1;
    double pps = 0;
    bool realtime = false;
    double speed = 1.0;
    uint64_t skip = 0, count = 0, first_seq = 1;
    size_t max_payload = mold::kMaxUdpPayload;
    mold::FaultConfig faults;
};

uint64_t to_u64(const char* v, const char* argv0) {
    char* end = nullptr;
    errno = 0;
    const unsigned long long x = std::strtoull(v, &end, 0);
    if (errno != 0 || end == v || *end != '\0' || v[0] == '-') usage(argv0);
    return x;
}

double to_double(const char* v, const char* argv0) {
    char* end = nullptr;
    const double x = std::strtod(v, &end);
    if (end == v || *end != '\0') usage(argv0);
    return x;
}

Args parse(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; ++i) {
        const std::string k = argv[i];
        if (k == "--realtime") {
            a.realtime = true;
            continue;
        }
        if (i + 1 >= argc) usage(argv[0]);
        const char* v = argv[++i];
        if (k == "--in")
            a.in = v;
        else if (k == "--dst")
            a.dst = v;
        else if (k == "--port")
            a.port = static_cast<int>(to_u64(v, argv[0]));
        else if (k == "--if-ip")
            a.if_ip = v;
        else if (k == "--out")
            a.out = v;
        else if (k == "--pps")
            a.pps = to_double(v, argv[0]);
        else if (k == "--speed")
            a.speed = to_double(v, argv[0]);
        else if (k == "--skip")
            a.skip = to_u64(v, argv[0]);
        else if (k == "--count")
            a.count = to_u64(v, argv[0]);
        else if (k == "--session")
            a.session = v;
        else if (k == "--first-seq")
            a.first_seq = to_u64(v, argv[0]);
        else if (k == "--max-payload")
            a.max_payload = to_u64(v, argv[0]);
        else if (k == "--drop-every")
            a.faults.drop_every = to_u64(v, argv[0]);
        else if (k == "--dup-every")
            a.faults.dup_every = to_u64(v, argv[0]);
        else if (k == "--swap-every")
            a.faults.swap_every = to_u64(v, argv[0]);
        else
            usage(argv[0]);
    }
    const bool to_net = !a.dst.empty();
    const bool ok = !a.in.empty() && (to_net != !a.out.empty()) &&
                    (!to_net || (a.port >= 1 && a.port <= 65535)) && a.pps >= 0 &&
                    !(a.pps > 0 && a.realtime) && a.speed > 0 && a.first_seq >= 1 &&
                    a.max_payload <= mold::kMaxUdpPayload;
    if (!ok) usage(argv[0]);
    return a;
}

class Sink {
   public:
    explicit Sink(const Args& a) {
        if (!a.out.empty()) {
            file_ = std::fopen(a.out.c_str(), "wb");
            if (file_ == nullptr) fail("cannot open " + a.out);
            return;
        }
        dst_.sin_family = AF_INET;
        dst_.sin_port = htons(static_cast<uint16_t>(a.port));
        if (inet_pton(AF_INET, a.dst.c_str(), &dst_.sin_addr) != 1) fail("bad --dst " + a.dst);
        fd_ = socket(AF_INET, SOCK_DGRAM, 0);
        if (fd_ < 0) fail(std::string("socket: ") + std::strerror(errno));
        const unsigned char ttl = 1, loop = 0;  // direct cable: never route, never loop back
        setsockopt(fd_, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof ttl);
        setsockopt(fd_, IPPROTO_IP, IP_MULTICAST_LOOP, &loop, sizeof loop);
        if (!a.if_ip.empty()) {
            in_addr ifa{};
            if (inet_pton(AF_INET, a.if_ip.c_str(), &ifa) != 1) fail("bad --if-ip " + a.if_ip);
            if (setsockopt(fd_, IPPROTO_IP, IP_MULTICAST_IF, &ifa, sizeof ifa) != 0)
                fail(std::string("IP_MULTICAST_IF: ") + std::strerror(errno));
        }
    }
    ~Sink() {
        if (file_ != nullptr) std::fclose(file_);
        if (fd_ >= 0) close(fd_);
    }
    Sink(const Sink&) = delete;
    Sink& operator=(const Sink&) = delete;

    void send(std::span<const uint8_t> b) {
        if (file_ != nullptr) {
            const uint8_t len[2] = {static_cast<uint8_t>(b.size() >> 8),
                                    static_cast<uint8_t>(b.size())};
            if (std::fwrite(len, 1, 2, file_) != 2 ||
                std::fwrite(b.data(), 1, b.size(), file_) != b.size())
                fail("write failed");
            return;
        }
        for (;;) {
            const ssize_t n = sendto(fd_, b.data(), b.size(), 0,
                                     reinterpret_cast<const sockaddr*>(&dst_), sizeof dst_);
            if (n >= 0) return;
            if (errno != ENOBUFS && errno != EAGAIN && errno != EINTR)
                fail(std::string("sendto: ") + std::strerror(errno));
            ++retries;
        }
    }
    void finish() {
        if (file_ != nullptr && std::fflush(file_) != 0) fail("write failed");
    }

    uint64_t retries = 0;

   private:
    [[noreturn]] static void fail(const std::string& why) { throw std::runtime_error(why); }
    FILE* file_ = nullptr;
    int fd_ = -1;
    sockaddr_in dst_{};
};

struct Stats {
    uint64_t messages = 0, sent = 0, payload_bytes = 0, wire = 0;
    int64_t max_lag_ns = 0;
};

class Pacer {
   public:
    Pacer(const Args& a, int64_t t0) : a_(a), t0_(t0) {}

    // Busy-waits until packet number k (0-based, in send order) is due.
    void wait(uint64_t k, uint64_t first_ts, Stats& st) {
        int64_t due = 0;
        if (a_.pps > 0) {
            due = t0_ + static_cast<int64_t>(static_cast<double>(k) * 1e9 / a_.pps);
        } else if (a_.realtime) {
            if (!have_ts0_) {
                ts0_ = first_ts;
                have_ts0_ = true;
            }
            // A swapped (late) packet has an earlier timestamp: it is simply due already.
            const double dt = first_ts > ts0_ ? static_cast<double>(first_ts - ts0_) : 0.0;
            due = t0_ + static_cast<int64_t>(dt / a_.speed);
        } else {
            return;
        }
        int64_t t = now_ns();
        st.max_lag_ns = std::max(st.max_lag_ns, t - due);
        while (t < due && !g_stop) t = now_ns();
    }

   private:
    const Args& a_;
    int64_t t0_;
    bool have_ts0_ = false;
    uint64_t ts0_ = 0;
};

int run(const Args& a) {
    mold::ItchReader reader(a.in);
    const mold::Session session = mold::make_session(a.session);
    mold::Packetizer packer(session, a.first_seq, a.max_payload);
    mold::FaultInjector faults(a.faults);
    Sink sink(a);

    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);

    Stats st;
    std::vector<uint8_t> msg;
    std::vector<mold::Packet> ready;
    uint64_t skipped = 0;
    const int64_t t0 = now_ns();
    Pacer pacer(a, t0);
    int64_t next_report = t0 + 1'000'000'000;
    uint64_t sent_at_report = 0;

    auto send_ready = [&] {
        for (const mold::Packet& p : ready) {
            if (g_stop) break;
            pacer.wait(st.sent, p.first_ts, st);
            sink.send(p.bytes);
            ++st.sent;
            st.payload_bytes += p.bytes.size();
            st.wire += wire_bytes(p.bytes.size());
            const int64_t t = now_ns();
            if (t >= next_report) {
                std::fprintf(stderr, "t=%.0fs sent=%llu pps=%llu msgs=%llu retries=%llu\n",
                             static_cast<double>(t - t0) / 1e9,
                             static_cast<unsigned long long>(st.sent),
                             static_cast<unsigned long long>(st.sent - sent_at_report),
                             static_cast<unsigned long long>(st.messages),
                             static_cast<unsigned long long>(sink.retries));
                sent_at_report = st.sent;
                next_report += 1'000'000'000;
            }
        }
        ready.clear();
    };

    bool eos = false;
    while (!g_stop && (a.count == 0 || st.messages < a.count) && reader.next(msg)) {
        if (skipped < a.skip) {
            ++skipped;
            continue;
        }
        ++st.messages;
        if (auto p = packer.push(msg)) faults.push(std::move(*p), ready);
        send_ready();
    }
    if (!g_stop) {
        if (auto p = packer.flush()) faults.push(std::move(*p), ready);
        faults.flush(ready);
        send_ready();
    }
    if (!g_stop) {
        // Not subject to faults: it marks where the feed ends.
        const std::vector<uint8_t> e = mold::end_of_session(session, packer.next_seq());
        sink.send(e);
        ++st.sent;
        st.payload_bytes += e.size();
        st.wire += wire_bytes(e.size());
        eos = true;
    }
    sink.finish();
    const double elapsed = static_cast<double>(now_ns() - t0) / 1e9;
    const double secs = elapsed > 0 ? elapsed : 1e-9;
    const mold::FaultCounters& fc = faults.counters();
    const char* mode = a.pps > 0 ? "pps" : a.realtime ? "realtime" : "flat_out";

    auto u = [](const char* k, uint64_t v) {
        std::printf("%s=%llu\n", k, static_cast<unsigned long long>(v));
    };
    std::printf("in=%s\n", a.in.c_str());
    if (a.out.empty())
        std::printf("dst=%s:%d\n", a.dst.c_str(), a.port);
    else
        std::printf("out=%s\n", a.out.c_str());
    std::printf("session=%s\n", a.session.c_str());
    std::printf("mode=%s\n", mode);
    if (a.pps > 0) std::printf("target_pps=%.1f\n", a.pps);
    if (a.realtime) std::printf("speed=%.3f\n", a.speed);
    u("skip", a.skip);
    u("first_seq", a.first_seq);
    u("messages", st.messages);
    u("next_seq", packer.next_seq());
    u("max_payload", a.max_payload);
    u("packets_built", fc.built);
    u("packets_sent", st.sent);
    u("dropped_packets", fc.dropped);
    u("dropped_messages", fc.dropped_messages);
    u("duplicated_packets", fc.duplicated);
    u("swapped_packets", fc.swapped);
    u("end_of_session", eos ? 1 : 0);
    u("send_retries", sink.retries);
    std::printf("elapsed_s=%.3f\n", elapsed);
    std::printf("achieved_pps=%.1f\n", static_cast<double>(st.sent) / secs);
    std::printf("achieved_msgs_per_s=%.1f\n", static_cast<double>(st.messages) / secs);
    std::printf("payload_mbps=%.3f\n", static_cast<double>(st.payload_bytes) * 8 / secs / 1e6);
    std::printf("wire_mbps=%.3f\n", static_cast<double>(st.wire) * 8 / secs / 1e6);
    if (a.realtime) std::printf("max_lag_ms=%.3f\n", static_cast<double>(st.max_lag_ns) / 1e6);
    std::printf("interrupted=%d\n", g_stop ? 1 : 0);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    const Args a = parse(argc, argv);
    try {
        return run(a);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "replay: %s\n", e.what());
        return 1;
    }
}
