// Unit tests for host/mold. Why they matter: the replayer defines the feed the FPGA
// decodes, and the golden model decodes the same file independently. A wrong length
// prefix, a little-endian sequence number, an off-by-one at the payload limit or a fault
// that hits the wrong packet would show up on hardware as an FPGA "mismatch" that is really
// a sender bug. The byte-exact cross-check against model/moldudp64.py is
// host/test_replay.py; these pin the edge cases directly.

#include <gtest/gtest.h>

#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "mold.hpp"

namespace {

using Bytes = std::vector<uint8_t>;

uint64_t be(const Bytes& b, size_t off, size_t n) {
    uint64_t v = 0;
    for (size_t i = 0; i < n; ++i) v = (v << 8) | b[off + i];
    return v;
}

Bytes msg_of(size_t len, uint8_t fill) { return Bytes(len, fill); }

// Messages of length len with an ITCH timestamp ts at offset 5.
Bytes itch_msg(size_t len, uint64_t ts) {
    Bytes m(len, 0);
    for (size_t i = 0; i < 6; ++i) m[5 + i] = static_cast<uint8_t>(ts >> (8 * (5 - i)));
    return m;
}

TEST(Session, PadsWithSpacesAndRejectsLong) {
    const mold::Session s = mold::make_session("AB");
    EXPECT_EQ(s[0], 'A');
    EXPECT_EQ(s[1], 'B');
    for (size_t i = 2; i < 10; ++i) EXPECT_EQ(s[i], ' ');
    EXPECT_NO_THROW(mold::make_session("0123456789"));
    EXPECT_THROW(mold::make_session("0123456789X"), std::invalid_argument);
}

TEST(Packetizer, HeaderIsBigEndianAndPrefixesAreLengths) {
    mold::Packetizer p(mold::make_session("S"), 0x0102030405060708ULL);
    EXPECT_FALSE(p.push(msg_of(3, 0xAA)));
    EXPECT_FALSE(p.push(msg_of(258, 0xBB)));  // 258 = 0x0102: catches a swapped prefix
    auto pk = p.flush();
    ASSERT_TRUE(pk);
    const Bytes& b = pk->bytes;
    ASSERT_EQ(b.size(), 20u + 2 + 3 + 2 + 258);
    EXPECT_EQ(b[0], 'S');
    EXPECT_EQ(be(b, 10, 8), 0x0102030405060708ULL);
    EXPECT_EQ(be(b, 18, 2), 2u);
    EXPECT_EQ(be(b, 20, 2), 3u);
    EXPECT_EQ(b[22], 0xAA);
    EXPECT_EQ(be(b, 25, 2), 258u);
    EXPECT_EQ(b[27], 0xBB);
    EXPECT_EQ(b.back(), 0xBB);
    EXPECT_EQ(p.next_seq(), 0x0102030405060708ULL + 2);
    EXPECT_FALSE(p.flush());  // nothing open
}

TEST(Packetizer, FillsExactlyToMaxPayloadThenSplits) {
    // 20 header + 2 x (2 + 23) = 70: exactly full at max_payload 70, one byte short at 69.
    for (size_t limit : {70u, 69u}) {
        mold::Packetizer p(mold::make_session("S"), 1, limit);
        EXPECT_FALSE(p.push(msg_of(23, 1)));
        auto closed = p.push(msg_of(23, 2));
        auto rest = p.flush();
        if (limit == 70) {
            EXPECT_FALSE(closed);
            ASSERT_TRUE(rest);
            EXPECT_EQ(rest->bytes.size(), 70u);
            EXPECT_EQ(rest->count, 2);
        } else {
            ASSERT_TRUE(closed);
            EXPECT_EQ(closed->count, 1);
            EXPECT_EQ(closed->first_seq, 1u);
            ASSERT_TRUE(rest);
            EXPECT_EQ(rest->first_seq, 2u);  // sequence continues across the split
            EXPECT_EQ(be(rest->bytes, 10, 8), 2u);
        }
    }
}

TEST(Packetizer, RejectsMessageThatCannotFitAlone) {
    mold::Packetizer p(mold::make_session("S"), 1, 100);
    EXPECT_NO_THROW(p.push(msg_of(78, 0)));  // 20 + 2 + 78 = 100
    EXPECT_THROW(p.push(msg_of(79, 0)), std::invalid_argument);
    EXPECT_THROW(mold::Packetizer(mold::make_session("S"), 1, 22), std::invalid_argument);
}

TEST(Packetizer, CapsCountBelowEndOfSessionMarker) {
    // Zero-length messages take 2 bytes each, so a huge payload limit is never the bound:
    // only the count cap stops the packet before 0xFFFF (which would read as end of session).
    mold::Packetizer p(mold::make_session("S"), 1, 1u << 20);
    std::optional<mold::Packet> closed;
    for (uint32_t i = 0; i < mold::kMaxCount + 1u; ++i) {
        auto c = p.push(Bytes{});
        if (c) {
            ASSERT_FALSE(closed);
            closed = std::move(c);
        }
    }
    ASSERT_TRUE(closed);
    EXPECT_EQ(closed->count, mold::kMaxCount);
    EXPECT_EQ(be(closed->bytes, 18, 2), mold::kMaxCount);
    EXPECT_EQ(p.flush()->count, 1);
}

TEST(Packetizer, FirstTimestampIsTheFirstMessages) {
    mold::Packetizer p(mold::make_session("S"), 1);
    p.push(itch_msg(19, 0x0000123456789AULL));
    p.push(itch_msg(19, 0x0000FFFFFFFFFFULL));
    EXPECT_EQ(p.flush()->first_ts, 0x0000123456789AULL);
    p.push(Bytes{'X'});  // too short for a timestamp: inherits the previous one
    EXPECT_EQ(p.flush()->first_ts, 0x0000FFFFFFFFFFULL);
}

TEST(EndOfSession, HeaderOnlyWithCountFFFF) {
    const Bytes e = mold::end_of_session(mold::make_session("S"), 42);
    ASSERT_EQ(e.size(), 20u);
    EXPECT_EQ(be(e, 10, 8), 42u);
    EXPECT_EQ(be(e, 18, 2), 0xFFFFu);
}

// Runs packets 1..n through the injector; returns the first_seq of each sent packet.
std::vector<uint64_t> run_faults(mold::FaultConfig cfg, uint64_t n, mold::FaultCounters* c) {
    mold::FaultInjector f(cfg);
    std::vector<mold::Packet> out;
    for (uint64_t i = 1; i <= n; ++i) {
        mold::Packet p;
        p.first_seq = i;
        p.count = 1;
        f.push(p, out);
    }
    f.flush(out);
    if (c != nullptr) *c = f.counters();
    std::vector<uint64_t> seqs;
    seqs.reserve(out.size());
    for (const auto& p : out) seqs.push_back(p.first_seq);
    return seqs;
}

TEST(Faults, NoneIsIdentity) {
    EXPECT_EQ(run_faults({}, 5, nullptr), (std::vector<uint64_t>{1, 2, 3, 4, 5}));
}

TEST(Faults, DropDupSwapHitTheNthPacket) {
    mold::FaultCounters c;
    EXPECT_EQ(run_faults({.drop_every = 3}, 7, &c), (std::vector<uint64_t>{1, 2, 4, 5, 7}));
    EXPECT_EQ(c.dropped, 2u);
    EXPECT_EQ(c.dropped_messages, 2u);
    EXPECT_EQ(run_faults({.dup_every = 2}, 4, &c), (std::vector<uint64_t>{1, 2, 2, 3, 4, 4}));
    EXPECT_EQ(c.duplicated, 2u);
    EXPECT_EQ(run_faults({.swap_every = 2}, 5, &c), (std::vector<uint64_t>{1, 3, 2, 5, 4}));
    EXPECT_EQ(c.swapped, 2u);  // packet 4 still goes out, at the end, via flush
}

TEST(Faults, SwapHeldAcrossADrop) {
    // 2 is held, 3 is dropped, so 2 goes out after 4.
    EXPECT_EQ(run_faults({.drop_every = 3, .swap_every = 2}, 4, nullptr),
              (std::vector<uint64_t>{1, 4, 2}));
}

TEST(Faults, RejectsEveryPacketDropOrSwap) {
    EXPECT_THROW(mold::FaultInjector({.drop_every = 1}), std::invalid_argument);
    EXPECT_THROW(mold::FaultInjector({.swap_every = 1}), std::invalid_argument);
    EXPECT_NO_THROW(mold::FaultInjector({.dup_every = 1}));
}

class ItchReaderTest : public ::testing::Test {
   protected:
    std::string path_ = ::testing::TempDir() + "itch_reader_test.bin";
    void write(const Bytes& b) {
        std::ofstream f(path_, std::ios::binary);
        f.write(reinterpret_cast<const char*>(b.data()), static_cast<std::streamsize>(b.size()));
        ASSERT_TRUE(f.good());
    }
    void TearDown() override { std::remove(path_.c_str()); }
};

TEST_F(ItchReaderTest, ReadsPlainLengthPrefixedMessages) {
    write({0x00, 0x02, 'A', 'B', 0x00, 0x00, 0x00, 0x01, 'C'});
    mold::ItchReader r(path_);
    Bytes m;
    ASSERT_TRUE(r.next(m));
    EXPECT_EQ(m, (Bytes{'A', 'B'}));
    ASSERT_TRUE(r.next(m));
    EXPECT_TRUE(m.empty());
    ASSERT_TRUE(r.next(m));
    EXPECT_EQ(m, (Bytes{'C'}));
    EXPECT_FALSE(r.next(m));
}

TEST_F(ItchReaderTest, TruncationThrows) {
    write({0x00, 0x03, 'A', 'B'});
    mold::ItchReader r(path_);
    Bytes m;
    EXPECT_THROW(r.next(m), std::runtime_error);
    write({0x00, 0x01, 'A', 0x00});
    mold::ItchReader r2(path_);
    ASSERT_TRUE(r2.next(m));
    EXPECT_THROW(r2.next(m), std::runtime_error);
}

TEST(ItchReader, MissingFileThrows) {
    EXPECT_THROW(mold::ItchReader("/nonexistent/x.itch"), std::runtime_error);
}

}  // namespace
