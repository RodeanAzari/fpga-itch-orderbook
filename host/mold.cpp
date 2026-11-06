#include "mold.hpp"

#include <zlib.h>

#include <stdexcept>

namespace mold {

namespace {

void put_be(std::vector<uint8_t>& b, size_t off, uint64_t v, size_t n) {
    for (size_t i = 0; i < n; ++i) b[off + n - 1 - i] = static_cast<uint8_t>(v >> (8 * i));
}

std::vector<uint8_t> header(const Session& s, uint64_t seq, uint16_t count) {
    std::vector<uint8_t> b(kHeaderLen);
    for (size_t i = 0; i < kSessionLen; ++i) b[i] = s[i];
    put_be(b, kSessionLen, seq, 8);
    put_be(b, kSessionLen + 8, count, 2);
    return b;
}

}  // namespace

Session make_session(const std::string& name) {
    if (name.size() > kSessionLen) throw std::invalid_argument("session longer than 10 bytes");
    Session s{};
    s.fill(' ');
    for (size_t i = 0; i < name.size(); ++i) s[i] = static_cast<uint8_t>(name[i]);
    return s;
}

std::optional<uint64_t> itch_timestamp(std::span<const uint8_t> msg) {
    if (msg.size() < kItchTsOffset + kItchTsLen) return std::nullopt;
    uint64_t ts = 0;
    for (size_t i = 0; i < kItchTsLen; ++i) ts = (ts << 8) | msg[kItchTsOffset + i];
    return ts;
}

Packetizer::Packetizer(Session session, uint64_t first_seq, size_t max_payload)
    : session_(session), max_payload_(max_payload), next_seq_(first_seq) {
    if (max_payload < kHeaderLen + kLenPrefix + 1)
        throw std::invalid_argument("max_payload too small for one message");
}

void Packetizer::open(uint64_t ts) {
    cur_ = Packet{};
    cur_.bytes = header(session_, next_seq_, 0);
    cur_.first_seq = next_seq_;
    cur_.first_ts = ts;
}

std::optional<Packet> Packetizer::push(std::span<const uint8_t> msg) {
    const size_t need = kLenPrefix + msg.size();
    if (kHeaderLen + need > max_payload_)
        throw std::invalid_argument("message of " + std::to_string(msg.size()) +
                                    " bytes cannot fit in " + std::to_string(max_payload_));
    // A message too short for a timestamp inherits the previous one (pacing only).
    const uint64_t ts = itch_timestamp(msg).value_or(last_ts_);
    last_ts_ = ts;

    std::optional<Packet> done;
    if (cur_.count > 0 && (cur_.bytes.size() + need > max_payload_ || cur_.count == kMaxCount))
        done = flush();
    if (cur_.count == 0) open(ts);

    const size_t off = cur_.bytes.size();
    cur_.bytes.resize(off + need);
    put_be(cur_.bytes, off, msg.size(), kLenPrefix);
    for (size_t i = 0; i < msg.size(); ++i) cur_.bytes[off + kLenPrefix + i] = msg[i];
    ++cur_.count;
    put_be(cur_.bytes, kSessionLen + 8, cur_.count, 2);
    ++next_seq_;
    return done;
}

std::optional<Packet> Packetizer::flush() {
    if (cur_.count == 0) return std::nullopt;
    Packet p = std::move(cur_);
    cur_ = Packet{};
    return p;
}

std::vector<uint8_t> end_of_session(const Session& session, uint64_t next_seq) {
    return header(session, next_seq, kCountEnd);
}

FaultInjector::FaultInjector(FaultConfig cfg) : cfg_(cfg) {
    if (cfg.drop_every == 1) throw std::invalid_argument("drop_every must be 0 or >= 2");
    if (cfg.swap_every == 1) throw std::invalid_argument("swap_every must be 0 or >= 2");
}

void FaultInjector::push(Packet p, std::vector<Packet>& out) {
    const uint64_t n = ++c_.built;
    auto hits = [n](uint64_t every) { return every != 0 && n % every == 0; };
    if (hits(cfg_.drop_every)) {
        ++c_.dropped;
        c_.dropped_messages += p.count;
        return;
    }
    if (hits(cfg_.swap_every) && !held_) {
        ++c_.swapped;
        held_ = std::move(p);
        return;
    }
    const bool dup = hits(cfg_.dup_every);
    if (dup) {
        ++c_.duplicated;
        out.push_back(p);
    }
    out.push_back(std::move(p));
    flush(out);
}

void FaultInjector::flush(std::vector<Packet>& out) {
    if (held_) {
        out.push_back(std::move(*held_));
        held_.reset();
    }
}

struct ItchReader::Impl {
    gzFile f = nullptr;
    std::string path;
};

ItchReader::ItchReader(const std::string& path) : impl_(std::make_unique<Impl>()) {
    impl_->path = path;
    impl_->f = gzopen(path.c_str(), "rb");  // reads plain files transparently
    if (impl_->f == nullptr) throw std::runtime_error("cannot open " + path);
    gzbuffer(impl_->f, 1 << 20);
}

ItchReader::~ItchReader() {
    if (impl_->f != nullptr) gzclose(impl_->f);
}

bool ItchReader::next(std::vector<uint8_t>& msg) {
    auto read = [this](uint8_t* dst, unsigned n) -> unsigned {
        const int got = gzread(impl_->f, dst, n);
        if (got < 0) {
            int err = 0;
            throw std::runtime_error(impl_->path + ": " + gzerror(impl_->f, &err));
        }
        return static_cast<unsigned>(got);
    };
    uint8_t prefix[kLenPrefix];
    const unsigned got = read(prefix, kLenPrefix);
    if (got == 0) return false;
    if (got != kLenPrefix) throw std::runtime_error(impl_->path + ": ends inside a length prefix");
    const unsigned len = (unsigned{prefix[0]} << 8) | prefix[1];
    msg.resize(len);
    if (len > 0 && read(msg.data(), len) != len)
        throw std::runtime_error(impl_->path + ": ends inside a message");
    return true;
}

}  // namespace mold
