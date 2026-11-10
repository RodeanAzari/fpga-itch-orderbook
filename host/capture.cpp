// capture: configures the FPGA and logs what it sends back (docs/protocol.md, buildplan §7).
//
// Sends one config (type 0x10) to --fpga:--ctrl-port from a local UDP socket, resending it
// every --retry-ms until the ack (type 0x11, pkt_seq 0, the config record echoed) arrives or
// --ack-timeout passes (exit 1). The FPGA sends its output to the config's source address,
// so the same socket then receives every datagram. --ready-file is created once the ack is
// in: start the replay only after that (protocol.md: messages before the ack are dropped).
//
// Log (--log FILE): every well-formed datagram from the FPGA from the ack on, as u16 BE
// length + payload (the framing of an ITCH file, so model.itch.iter_raw reads it back and
// model.protocol.parse splits it), flushed per datagram so it can be followed live. Not
// logged, only counted: datagrams before the ack,
// datagrams from any other source, and malformed ones (header, type, record_len or length
// wrong; a config type; stats without all 40 counters).
//
// pkt_seq must rise by 1 per datagram after the ack (0). A jump counts seq_gaps and the
// datagrams lost (seq_lost); a lower or equal value counts seq_backwards (reordered or
// duplicated). Another ack after ours counts other_acks: another host reconfigured the FPGA.
//
// Stops on Ctrl-C, after --duration seconds, or after --idle seconds without a BBO/trace
// datagram once one has arrived. Stats arrive once per second, so --idle >= 2 makes the
// log end with a stats snapshot taken after the last record.
//
// Output: a progress line per second on stderr; a key=value summary on stdout for the
// results sidecar, ending with the last stats snapshot by counter name.

#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <exception>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

// docs/protocol.md, "Common header" and the packet-type table.
constexpr uint16_t kMagic = 0x4F42;
constexpr uint8_t kVersion = 1;
constexpr size_t kHeaderLen = 12;
constexpr size_t kMaxPayload = 1472;
constexpr uint8_t kBbo = 0x01, kTrace = 0x02, kStats = 0x03, kConfig = 0x10, kAck = 0x11;
constexpr size_t kConfigLen = 24;

// docs/protocol.md, "Stats": the counters in record order.
constexpr std::array<const char*, 40> kStatsNames = {"uptime_cycles",
                                                     "rx_frames_ok",
                                                     "rx_crc_err",
                                                     "rx_err",
                                                     "rx_runt",
                                                     "rx_oversize",
                                                     "rx_fifo_overflow",
                                                     "feed_datagrams",
                                                     "feed_filtered",
                                                     "ip_csum_err",
                                                     "udp_malformed",
                                                     "mold_packets",
                                                     "mold_heartbeats",
                                                     "mold_end_of_session",
                                                     "mold_gaps",
                                                     "mold_gap_messages",
                                                     "mold_duplicates",
                                                     "mold_session_changes",
                                                     "mold_malformed_packets",
                                                     "itch_unknown_types",
                                                     "itch_malformed_messages",
                                                     "itch_other_locate",
                                                     "itch_directory_matches",
                                                     "itch_locate_changes",
                                                     "locate",
                                                     "book_records",
                                                     "ref_overflow",
                                                     "duplicate_ref",
                                                     "shares_overflow",
                                                     "table_overflow",
                                                     "unknown_ref",
                                                     "over_reduce",
                                                     "price_oow",
                                                     "price_subpenny",
                                                     "bbo_oow",
                                                     "stale",
                                                     "out_records",
                                                     "out_datagrams",
                                                     "ctrl_ok",
                                                     "ctrl_bad"};

size_t record_len(uint8_t type) {
    switch (type) {
        case kBbo:
            return 64;
        case kTrace:
            return 40;
        case kStats:
            return 8;
        case kConfig:
        case kAck:
            return kConfigLen;
        default:
            return 0;
    }
}

uint64_t get_be(std::span<const uint8_t> b, size_t off, size_t n) {
    uint64_t v = 0;
    for (size_t i = 0; i < n; ++i) v = (v << 8) | b[off + i];
    return v;
}

void put_be(std::vector<uint8_t>& out, uint64_t v, size_t n) {
    for (size_t i = n; i-- > 0;) out.push_back(static_cast<uint8_t>(v >> (8 * i)));
}

struct Header {
    uint8_t type;
    uint32_t pkt_seq;
    uint16_t count;
};

// The checks of model.protocol.parse: false for anything the protocol does not allow.
bool parse_header(std::span<const uint8_t> p, Header& h) {
    if (p.size() < kHeaderLen) return false;
    h.type = p[3];
    h.pkt_seq = static_cast<uint32_t>(get_be(p, 4, 4));
    h.count = static_cast<uint16_t>(get_be(p, 8, 2));
    const size_t n = record_len(h.type);
    return get_be(p, 0, 2) == kMagic && p[2] == kVersion && n != 0 && get_be(p, 10, 2) == n &&
           h.count >= 1 && h.count <= (kMaxPayload - kHeaderLen) / n &&
           p.size() == kHeaderLen + h.count * n;
}

volatile std::sig_atomic_t g_stop = 0;
void on_signal(int) { g_stop = 1; }

int64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1'000'000'000 + ts.tv_nsec;
}

[[noreturn]] void usage(const char* argv0) {
    std::fprintf(stderr,
                 "usage: %s --log FILE --lo-price N [--symbol SYM] [--mode bbo|trace]\n"
                 "          [--locate N] [--group IP] [--feed-port N] [--fpga IP]\n"
                 "          [--ctrl-port N] [--bind-ip IP] [--ready-file FILE]\n"
                 "          [--retry-ms N] [--ack-timeout S] [--idle S] [--duration S]\n"
                 "  --log          datagram log (u16 BE length + payload)\n"
                 "  --lo-price     price window's low edge, ITCH price units (ADR 0002)\n"
                 "  --symbol       up to 8 characters (default AAPL)\n"
                 "  --mode         bbo (default) or trace\n"
                 "  --locate       use this locate (else the FPGA learns it from R)\n"
                 "  --group/--feed-port  feed address (default 239.1.1.1:26400)\n"
                 "  --fpga/--ctrl-port   FPGA control address (default 192.168.10.2:7000)\n"
                 "  --bind-ip      local address to send from and receive on (default any)\n"
                 "  --ready-file   created when the ack arrives\n"
                 "  --retry-ms     config resend interval (default 500)\n"
                 "  --ack-timeout  give up without an ack after S seconds (default 5)\n"
                 "  --idle         stop S seconds after the last BBO/trace datagram (0 = off)\n"
                 "  --duration     stop after S seconds (0 = off)\n",
                 argv0);
    std::exit(2);
}

struct Args {
    std::string log, symbol = "AAPL", group = "239.1.1.1", fpga = "192.168.10.2", bind_ip,
                     ready_file;
    int feed_port = 26400, ctrl_port = 7000;
    uint8_t mode = 0;
    bool locate_valid = false;
    uint64_t locate = 0, lo_price = 0, retry_ms = 500;
    bool have_lo_price = false;
    double ack_timeout = 5, idle = 0, duration = 0;
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
    if (end == v || *end != '\0' || !(x >= 0)) usage(argv0);
    return x;
}

Args parse(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; ++i) {
        const std::string k = argv[i];
        if (i + 1 >= argc) usage(argv[0]);
        const char* v = argv[++i];
        if (k == "--log")
            a.log = v;
        else if (k == "--symbol")
            a.symbol = v;
        else if (k == "--mode") {
            const std::string m = v;
            if (m != "bbo" && m != "trace") usage(argv[0]);
            a.mode = m == "trace" ? 1 : 0;
        } else if (k == "--locate") {
            a.locate = to_u64(v, argv[0]);
            a.locate_valid = true;
        } else if (k == "--lo-price") {
            a.lo_price = to_u64(v, argv[0]);
            a.have_lo_price = true;
        } else if (k == "--group")
            a.group = v;
        else if (k == "--feed-port")
            a.feed_port = static_cast<int>(to_u64(v, argv[0]));
        else if (k == "--fpga")
            a.fpga = v;
        else if (k == "--ctrl-port")
            a.ctrl_port = static_cast<int>(to_u64(v, argv[0]));
        else if (k == "--bind-ip")
            a.bind_ip = v;
        else if (k == "--ready-file")
            a.ready_file = v;
        else if (k == "--retry-ms")
            a.retry_ms = to_u64(v, argv[0]);
        else if (k == "--ack-timeout")
            a.ack_timeout = to_double(v, argv[0]);
        else if (k == "--idle")
            a.idle = to_double(v, argv[0]);
        else if (k == "--duration")
            a.duration = to_double(v, argv[0]);
        else
            usage(argv[0]);
    }
    const bool ok = !a.log.empty() && a.have_lo_price && a.lo_price <= 0xFFFFFFFF &&
                    !a.symbol.empty() && a.symbol.size() <= 8 && a.locate <= 0xFFFF &&
                    a.feed_port >= 1 && a.feed_port <= 65535 && a.ctrl_port >= 1 &&
                    a.ctrl_port <= 65535 && a.retry_ms >= 1;
    if (!ok) usage(argv[0]);
    return a;
}

in_addr to_addr(const std::string& s, const char* what) {
    in_addr x{};
    if (inet_pton(AF_INET, s.c_str(), &x) != 1)
        throw std::runtime_error(std::string("bad ") + what + " " + s);
    return x;
}

// docs/protocol.md, "Config": one 24-byte record.
std::vector<uint8_t> config_record(const Args& a) {
    std::vector<uint8_t> r;
    put_be(r, ntohl(to_addr(a.group, "--group").s_addr), 4);
    if ((r[0] & 0xF0) != 0xE0) throw std::runtime_error("--group is not multicast: " + a.group);
    put_be(r, static_cast<uint64_t>(a.feed_port), 2);
    r.push_back(a.mode);
    r.push_back(a.locate_valid ? 1 : 0);
    std::string sym = a.symbol;
    sym.resize(8, ' ');
    r.insert(r.end(), sym.begin(), sym.end());
    put_be(r, a.lo_price, 4);
    put_be(r, a.locate, 2);
    put_be(r, 0, 2);
    return r;
}

std::vector<uint8_t> config_packet(const std::vector<uint8_t>& rec) {
    std::vector<uint8_t> p;
    put_be(p, kMagic, 2);
    p.push_back(kVersion);
    p.push_back(kConfig);
    put_be(p, 0, 4);  // pkt_seq: ignored by the FPGA
    put_be(p, 1, 2);
    put_be(p, kConfigLen, 2);
    p.insert(p.end(), rec.begin(), rec.end());
    return p;
}

class Log {
   public:
    explicit Log(const std::string& path) : f_(std::fopen(path.c_str(), "wb")) {
        if (f_ == nullptr) throw std::runtime_error("cannot open " + path);
    }
    ~Log() { std::fclose(f_); }
    Log(const Log&) = delete;
    Log& operator=(const Log&) = delete;

    void write(std::span<const uint8_t> b) {
        const uint8_t len[2] = {static_cast<uint8_t>(b.size() >> 8),
                                static_cast<uint8_t>(b.size())};
        // Flushed per datagram so a live reader (analysis/bbo_view.py) sees it at once.
        if (std::fwrite(len, 1, 2, f_) != 2 || std::fwrite(b.data(), 1, b.size(), f_) != b.size() ||
            std::fflush(f_) != 0)
            throw std::runtime_error("log write failed");
    }
    void finish() {
        if (std::fflush(f_) != 0) throw std::runtime_error("log write failed");
    }

   private:
    FILE* f_;
};

class Socket {
   public:
    explicit Socket(const Args& a) : fd_(socket(AF_INET, SOCK_DGRAM, 0)) {
        if (fd_ < 0) throw std::runtime_error(std::string("socket: ") + std::strerror(errno));
        sockaddr_in local{};
        local.sin_family = AF_INET;
        local.sin_addr.s_addr =
            a.bind_ip.empty() ? htonl(INADDR_ANY) : to_addr(a.bind_ip, "--bind-ip").s_addr;
        if (bind(fd_, reinterpret_cast<const sockaddr*>(&local), sizeof local) != 0)
            throw std::runtime_error(std::string("bind: ") + std::strerror(errno));
        const int rcvbuf = 8 << 20;  // a full-speed burst must not overflow the kernel buffer
        setsockopt(fd_, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof rcvbuf);
    }
    ~Socket() { close(fd_); }
    Socket(const Socket&) = delete;
    Socket& operator=(const Socket&) = delete;

    void send_to(std::span<const uint8_t> b, const sockaddr_in& dst) const {
        if (sendto(fd_, b.data(), b.size(), 0, reinterpret_cast<const sockaddr*>(&dst),
                   sizeof dst) < 0)
            throw std::runtime_error(std::string("sendto: ") + std::strerror(errno));
    }

    // Waits up to timeout_ms; returns the datagram size, or -1 if none arrived.
    ssize_t recv(std::vector<uint8_t>& buf, sockaddr_in& src, int timeout_ms) const {
        pollfd pfd{fd_, POLLIN, 0};
        const int r = poll(&pfd, 1, timeout_ms);
        if (r < 0 && errno != EINTR)
            throw std::runtime_error(std::string("poll: ") + std::strerror(errno));
        if (r <= 0) return -1;
        socklen_t sl = sizeof src;
        const ssize_t n =
            recvfrom(fd_, buf.data(), buf.size(), 0, reinterpret_cast<sockaddr*>(&src), &sl);
        if (n < 0 && errno != EINTR)
            throw std::runtime_error(std::string("recvfrom: ") + std::strerror(errno));
        return n;
    }

   private:
    int fd_;
};

struct Counts {
    uint64_t datagrams = 0, bbo = 0, trace = 0, stats = 0, records = 0;
    uint64_t pre_ack = 0, foreign = 0, malformed = 0, other_acks = 0;
    uint64_t seq_gaps = 0, seq_lost = 0, seq_backwards = 0, config_sends = 0;
};

int run(const Args& a) {
    const std::vector<uint8_t> rec = config_record(a);
    const std::vector<uint8_t> cfg = config_packet(rec);
    sockaddr_in fpga{};
    fpga.sin_family = AF_INET;
    fpga.sin_port = htons(static_cast<uint16_t>(a.ctrl_port));
    fpga.sin_addr = to_addr(a.fpga, "--fpga");

    Log log(a.log);
    Socket sock(a);
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);

    Counts c;
    std::vector<uint8_t> buf(65536);
    std::vector<uint64_t> last_stats;
    sockaddr_in src{};
    auto from_fpga = [&] {
        return src.sin_addr.s_addr == fpga.sin_addr.s_addr && src.sin_port == fpga.sin_port;
    };

    // Phase 1: config until acked.
    const int64_t t0 = now_ns();
    int64_t next_send = t0;
    bool acked = false;
    while (!acked && !g_stop) {
        int64_t t = now_ns();
        if (static_cast<double>(t - t0) / 1e9 >= a.ack_timeout) break;
        if (t >= next_send) {
            sock.send_to(cfg, fpga);
            ++c.config_sends;
            next_send = t + static_cast<int64_t>(a.retry_ms) * 1'000'000;
        }
        const ssize_t n = sock.recv(buf, src, 10);
        if (n < 0) continue;
        const std::span<const uint8_t> p(buf.data(), static_cast<size_t>(n));
        Header h{};
        if (!from_fpga()) {
            ++c.foreign;
        } else if (!parse_header(p, h)) {
            ++c.malformed;
        } else if (h.type == kAck && h.pkt_seq == 0 &&
                   std::equal(rec.begin(), rec.end(), p.begin() + kHeaderLen)) {
            log.write(p);
            ++c.datagrams;
            acked = true;
        } else {
            ++c.pre_ack;  // an earlier run's output, or an ack of another config
        }
    }
    if (!acked) {
        std::fprintf(stderr, "capture: no ack after %llu config sends\n",
                     static_cast<unsigned long long>(c.config_sends));
        return 1;
    }
    const int64_t t_ack = now_ns();
    std::fprintf(stderr, "capture: acked after %.3f s\n", static_cast<double>(t_ack - t0) / 1e9);
    if (!a.ready_file.empty()) {
        FILE* f = std::fopen(a.ready_file.c_str(), "w");
        if (f == nullptr) throw std::runtime_error("cannot create " + a.ready_file);
        std::fclose(f);
    }

    // Phase 2: log until stopped.
    uint32_t expect = 1;
    int64_t last_rec = 0, next_report = t_ack + 1'000'000'000;
    uint64_t rec_at_report = 0;
    while (!g_stop) {
        const int64_t t = now_ns();
        if (a.duration > 0 && static_cast<double>(t - t_ack) / 1e9 >= a.duration) break;
        if (a.idle > 0 && last_rec != 0 && static_cast<double>(t - last_rec) / 1e9 >= a.idle) break;
        if (t >= next_report) {
            std::fprintf(stderr, "t=%.0fs datagrams=%llu records=%llu rec/s=%llu seq_lost=%llu\n",
                         static_cast<double>(t - t_ack) / 1e9,
                         static_cast<unsigned long long>(c.datagrams),
                         static_cast<unsigned long long>(c.records),
                         static_cast<unsigned long long>(c.records - rec_at_report),
                         static_cast<unsigned long long>(c.seq_lost));
            rec_at_report = c.records;
            next_report += 1'000'000'000;
        }
        const ssize_t n = sock.recv(buf, src, 10);
        if (n < 0) continue;
        const std::span<const uint8_t> p(buf.data(), static_cast<size_t>(n));
        Header h{};
        if (!from_fpga()) {
            ++c.foreign;
            continue;
        }
        // A config is never sent by the FPGA; stats always carry every counter.
        if (!parse_header(p, h) || h.type == kConfig ||
            (h.type == kStats && h.count != kStatsNames.size())) {
            ++c.malformed;
            continue;
        }
        log.write(p);
        ++c.datagrams;
        if (h.type == kAck) {
            ++c.other_acks;
            expect = 1;
            continue;
        }
        if (h.pkt_seq > expect) {
            ++c.seq_gaps;
            c.seq_lost += h.pkt_seq - expect;
        }
        if (h.pkt_seq < expect)
            ++c.seq_backwards;
        else
            expect = h.pkt_seq + 1;
        if (h.type == kBbo || h.type == kTrace) {
            ++(h.type == kBbo ? c.bbo : c.trace);
            c.records += h.count;
            last_rec = now_ns();
        } else {
            ++c.stats;
            last_stats.clear();
            for (size_t i = 0; i < h.count; ++i)
                last_stats.push_back(get_be(p, kHeaderLen + 8 * i, 8));
        }
    }
    log.finish();

    auto u = [](const char* k, uint64_t v) {
        std::printf("%s=%llu\n", k, static_cast<unsigned long long>(v));
    };
    std::printf("log=%s\n", a.log.c_str());
    std::printf("fpga=%s:%d\n", a.fpga.c_str(), a.ctrl_port);
    std::printf("feed=%s:%d\n", a.group.c_str(), a.feed_port);
    std::printf("mode=%s\n", a.mode == 1 ? "trace" : "bbo");
    std::printf("symbol=%s\n", a.symbol.c_str());
    u("lo_price", a.lo_price);
    if (a.locate_valid) u("locate_cfg", a.locate);
    u("config_sends", c.config_sends);
    u("datagrams", c.datagrams);
    u("bbo_datagrams", c.bbo);
    u("trace_datagrams", c.trace);
    u("stats_datagrams", c.stats);
    u("records", c.records);
    u("seq_gaps", c.seq_gaps);
    u("seq_lost", c.seq_lost);
    u("seq_backwards", c.seq_backwards);
    u("other_acks", c.other_acks);
    u("pre_ack_ignored", c.pre_ack);
    u("foreign", c.foreign);
    u("malformed", c.malformed);
    std::printf("elapsed_s=%.3f\n", static_cast<double>(now_ns() - t_ack) / 1e9);
    std::printf("interrupted=%d\n", g_stop ? 1 : 0);
    for (size_t i = 0; i < last_stats.size(); ++i) {
        const std::string k = std::string("stats.") + kStatsNames.at(i);
        u(k.c_str(), last_stats[i]);
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    const Args a = parse(argc, argv);
    try {
        return run(a);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "capture: %s\n", e.what());
        return 1;
    }
}
