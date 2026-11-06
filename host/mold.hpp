// MoldUDP64 packet building for the host replayer (docs/buildplan.md §5.2, §7).
//
// Mirrors model/moldudp64.py: header = session (10 bytes) | first sequence number (u64 BE)
// | message count (u16 BE), then each message as u16 BE length + bytes. Packets are filled
// greedily up to max_payload bytes. The packetization does not depend on pacing, so the
// expected stream for a run is fully determined by the input file and these settings.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace mold {

inline constexpr size_t kSessionLen = 10;
inline constexpr size_t kHeaderLen = kSessionLen + 8 + 2;  // 20
inline constexpr size_t kLenPrefix = 2;
inline constexpr size_t kMaxUdpPayload = 1472;  // 1500 MTU - 20 IPv4 - 8 UDP
inline constexpr uint16_t kCountEnd = 0xFFFF;   // end of session
inline constexpr uint16_t kMaxCount = kCountEnd - 1;
inline constexpr size_t kItchTsOffset = 5;  // ITCH 5.0 common header: timestamp, offset 5
inline constexpr size_t kItchTsLen = 6;

using Session = std::array<uint8_t, kSessionLen>;

// Session name, space-padded to 10 bytes. Throws std::invalid_argument if longer.
Session make_session(const std::string& name);

struct Packet {
    std::vector<uint8_t> bytes;
    uint64_t first_seq = 0;
    uint16_t count = 0;
    uint64_t first_ts = 0;  // ITCH timestamp (ns since midnight) of the first message
};

// ITCH 5.0 timestamp of one message, or nullopt if it is too short to carry one.
std::optional<uint64_t> itch_timestamp(std::span<const uint8_t> msg);

class Packetizer {
   public:
    // Throws std::invalid_argument if max_payload cannot hold the header and a 1-byte message.
    Packetizer(Session session, uint64_t first_seq, size_t max_payload = kMaxUdpPayload);

    // Adds msg. If it does not fit in the open packet, the open packet is closed and
    // returned, and msg starts the next one. Throws std::invalid_argument if msg alone
    // cannot fit in max_payload.
    std::optional<Packet> push(std::span<const uint8_t> msg);

    // Closes and returns the open packet, if it holds any message.
    std::optional<Packet> flush();

    // Sequence number of the next message not yet in a closed or open packet.
    uint64_t next_seq() const { return next_seq_; }

   private:
    void open(uint64_t ts);

    Session session_;
    size_t max_payload_;
    uint64_t next_seq_;
    Packet cur_;
    uint64_t last_ts_ = 0;
};

// Header-only packet with count 0xFFFF and the next expected sequence number.
std::vector<uint8_t> end_of_session(const Session& session, uint64_t next_seq);

// Deterministic fault injection on the packet stream, numbered 1, 2, 3, ... in build order.
// Packet n is dropped if drop_every divides n; otherwise held and sent after the next sent
// packet if swap_every divides n; otherwise sent, twice in a row if dup_every divides n.
// 0 disables a fault. Drop and swap need N >= 2 (N = 1 would drop or hold everything).
struct FaultConfig {
    uint64_t drop_every = 0;
    uint64_t dup_every = 0;
    uint64_t swap_every = 0;
};

struct FaultCounters {
    uint64_t built = 0;
    uint64_t dropped = 0;
    uint64_t dropped_messages = 0;
    uint64_t duplicated = 0;
    uint64_t swapped = 0;
};

class FaultInjector {
   public:
    // Throws std::invalid_argument for drop_every == 1 or swap_every == 1.
    explicit FaultInjector(FaultConfig cfg);

    // Appends the packets to send now, in order.
    void push(Packet p, std::vector<Packet>& out);

    // Appends a still-held packet, if any. Call once after the last push.
    void flush(std::vector<Packet>& out);

    const FaultCounters& counters() const { return c_; }

   private:
    FaultConfig cfg_;
    FaultCounters c_;
    std::optional<Packet> held_;
};

// Reads a length-prefixed ITCH stream (u16 BE length + message), gzip or plain.
class ItchReader {
   public:
    // Throws std::runtime_error if the file cannot be opened.
    explicit ItchReader(const std::string& path);
    ~ItchReader();
    ItchReader(const ItchReader&) = delete;
    ItchReader& operator=(const ItchReader&) = delete;

    // Reads the next message into msg. Returns false at a clean end of stream. Throws
    // std::runtime_error on a read error or a stream that ends inside a message.
    bool next(std::vector<uint8_t>& msg);

   private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace mold
