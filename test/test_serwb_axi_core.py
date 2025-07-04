#!/usr/bin/env python3

#
# This file is part of LiteICLink.
#
# Copyright (c) 2017-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random

from migen import *

from litex.gen import *

from litex.gen.sim import *

from litex.soc.interconnect import stream, axi

from liteiclink.serwb import scrambler
from liteiclink.serwb.core import SERWBCoreAXILite

from litex.soc.interconnect.axi import *

# Fake Init/Serdes/PHY -----------------------------------------------------------------------------

class FakeInit(LiteXModule):
    def __init__(self):
        self.ready = Signal(reset=1)


class FakeSerdes(LiteXModule):
    def __init__(self, dw):
        self.tx_ce = Signal()
        self.tx_k  = Signal(4)
        self.tx_d  = Signal(dw)
        self.rx_ce = Signal()
        self.rx_k  = Signal(4)
        self.rx_d  = Signal(dw)

        # # #

        data_ce = Signal(5, reset=0b00001)
        self.sync += data_ce.eq(Cat(data_ce[1:], data_ce[0]))

        self.comb += [
            self.tx_ce.eq(data_ce[0]),
            self.rx_ce.eq(data_ce[0])
        ]

class FakePHY(LiteXModule):
    def __init__(self, dw:int):
        self.sink   = sink   = stream.Endpoint([("data", dw)])
        self.source = source = stream.Endpoint([("data", dw)])

        # # #

        self.init   = FakeInit()
        self.serdes = FakeSerdes(dw)

        # TX dataflow
        self.comb += [
            If(self.init.ready,
                sink.ready.eq(self.serdes.tx_ce),
                If(sink.valid,
                    self.serdes.tx_d.eq(sink.data)
                )
            )
        ]

        # RX dataflow
        self.comb += [
            If(self.init.ready,
                source.valid.eq(self.serdes.rx_ce),
                source.data.eq(self.serdes.rx_d)
            )
        ]

# DUT Scrambler ------------------------------------------------------------------------------------

class DUTScrambler(LiteXModule):
    def __init__(self):
        self.scrambler   = scrambler.Scrambler(sync_interval=16)
        self.descrambler = scrambler.Descrambler()
        self.comb += self.scrambler.source.connect(self.descrambler.sink)

# DUT Core -----------------------------------------------------------------------------------------

class DUTCore(LiteXModule):
    def __init__(self, **kwargs):
        # AXI slave
        phy_slaves = {k:FakePHY(dw=32) for k in ['aw', 'ar', 'w', 'b', 'r']}
        serwb_slave = SERWBCoreAXILite(phy_slaves, int(1e6), mode="slave")
        self.submodules += serwb_slave


        # AXI master

        phy_masters = {k:FakePHY(dw=32) for k in ['aw', 'ar', 'w', 'b', 'r']}
        serwb_master = SERWBCoreAXILite(phy_masters, int(1e6), mode="master")
        self.submodules += serwb_master
        for k in ['aw', 'w', 'ar', 'r', 'b']:
            self.submodules += phy_slaves[k], phy_masters[k]
            # Connect phy
            self.comb += [
                phy_masters[k].serdes.rx_ce.eq(phy_slaves[k].serdes.tx_ce),
                phy_masters[k].serdes.rx_k.eq(phy_slaves[k].serdes.tx_k),
                phy_masters[k].serdes.rx_d.eq(phy_slaves[k].serdes.tx_d),

                phy_slaves[k].serdes.rx_ce.eq(phy_masters[k].serdes.tx_ce),
                phy_slaves[k].serdes.rx_k.eq(phy_masters[k].serdes.tx_k),
                phy_slaves[k].serdes.rx_d.eq(phy_masters[k].serdes.tx_d)
            ]

        # Add AXI sram to AXI master
        sram = AXILiteSRAM(1024, bus=serwb_master.bus)
        self.submodules += sram

        # Expose AXI slave
        self.axi = serwb_slave.bus

# DUT AXIFullCore -----------------------------------------------------------------------------------------

class DUTAXIFullCore(LiteXModule):
    def __init__(self, dw=32,**kwargs):
        # AXI slave

        self.axi = AXIInterface(data_width=dw, id_width=8)
        phy_slaves = {k:FakePHY(dw=32) for k in ['aw', 'ar', 'w', 'b', 'r']}
        serwb_slave = SERWBCoreAXILite(phy_slaves, int(1e6), mode="slave", axi_interface=self.axi)
        self.submodules += serwb_slave


        # AXI master

        phy_masters = {k:FakePHY(dw=32) for k in ['aw', 'ar', 'w', 'b', 'r']}
        self.axi_out = AXIInterface(data_width=dw, id_width=8)
        serwb_master = SERWBCoreAXILite(phy_masters, int(1e6), mode="master", axi_interface=self.axi_out)
        self.submodules += serwb_master
        for k in ['aw', 'w', 'ar', 'r', 'b']:
            self.submodules += phy_slaves[k], phy_masters[k]
            # Connect phy
            self.comb += [
                phy_masters[k].serdes.rx_ce.eq(phy_slaves[k].serdes.tx_ce),
                phy_masters[k].serdes.rx_k.eq(phy_slaves[k].serdes.tx_k),
                phy_masters[k].serdes.rx_d.eq(phy_slaves[k].serdes.tx_d),

                phy_slaves[k].serdes.rx_ce.eq(phy_masters[k].serdes.tx_ce),
                phy_slaves[k].serdes.rx_k.eq(phy_masters[k].serdes.tx_k),
                phy_slaves[k].serdes.rx_d.eq(phy_masters[k].serdes.tx_d)
            ]

        # Add AXI sram to AXI master
        self.ram_bus = AXILiteInterface(data_width=dw)
        self.axi2axilite = AXI2AXILite(self.axi_out, self.ram_bus)
        self.submodules += self.axi2axilite
        sram = AXILiteSRAM(1024*2, bus=self.ram_bus)
        self.submodules += sram


# Software Models ----------------------------------------------------------------------------------

class Burst:
    def __init__(self, addr, type=BURST_FIXED, len=0, size=0):
        self.addr = addr
        self.type = type
        self.len  = len
        self.size = size

    def to_beats(self):
        r = []
        burst_length = self.len + 1
        burst_size   = 2**self.size
        for i in range(burst_length):
            if self.type == BURST_INCR:
                offset = i*2**(self.size)
                r += [Beat(self.addr + offset)]
            elif self.type == BURST_WRAP:
                assert burst_length in [2, 4, 8, 16]
                assert (self.addr % burst_size) == 0
                burst_base   = self.addr - self.addr % (burst_length * burst_size)
                burst_offset = self.addr % (burst_length * burst_size)
                burst_addr   = burst_base + (burst_offset + i*burst_size) % (burst_length * burst_size)
                #print("0x{:08x}".format(burst_addr))
                r += [Beat(burst_addr)]
            else:
                r += [Beat(self.addr)]
        return r


class Beat:
    def __init__(self, addr):
        self.addr = addr

class Access(Burst):
    def __init__(self, addr, data, id, **kwargs):
        Burst.__init__(self, addr, **kwargs)
        self.data = data
        self.id   = id


class Write(Access): pass

class Read(Access): pass

# Test SERWB Core ----------------------------------------------------------------------------------

class TestSERWBCore(unittest.TestCase):
    def test_scrambler(self):
        def generator(dut, rand_level=50):
            # Prepare test
            prng      = random.Random(42)
            i         = 0
            last_data = -1
            # Test loop
            while i != 256:
                # Stim
                yield dut.scrambler.sink.valid.eq(1)
                if (yield dut.scrambler.sink.valid) & (yield dut.scrambler.sink.ready):
                    i += 1
                yield dut.scrambler.sink.data.eq(i)

                # Check
                yield dut.descrambler.source.ready.eq(prng.randrange(100) > rand_level)
                if (yield dut.descrambler.source.valid) & (yield dut.descrambler.source.ready):
                    current_data = (yield dut.descrambler.source.data)
                    if (current_data != (last_data + 1)):
                        dut.errors += 1
                    last_data = current_data

                # Cycle
                yield

        dut = DUTScrambler()
        dut.errors = 0
        run_simulation(dut, generator(dut))
        self.assertEqual(dut.errors, 0)

    def test_serwb(self):
        def generator(dut):
            # Prepare test
            prng        = random.Random(42)
            data_base   = 0x100
            data_length = 6
            datas_w     = [prng.randrange(2**32) for i in range(data_length)]
            datas_r     = []

            # Write
            for i in range(data_length):
                print("Write %d" % i, end='\r')
                yield from dut.axi.write((data_base + i*4), datas_w[i])

            # Read
            for i in range(data_length):
                print("Read %d" % i, end='\r')
                datas_r.append((yield from dut.axi.read((data_base + i*4)))[0])
            # Check
            print(datas_w)
            print(datas_r)
            for i in range(data_length):
                if datas_r[i] != datas_w[i]:
                    dut.errors += 1

            datas_w     = [prng.randrange(2**32) for i in range(data_length)]
            datas_r     = []
            # Alternate Read Write
            for i in range(data_length):
                yield from dut.axi.write((data_base + i*4), datas_w[i])
                datas_r.append((yield from dut.axi.read((data_base + i*4)))[0])
            # Check
            print(datas_w)
            print(datas_r)
            for i in range(data_length):
                if datas_r[i] != datas_w[i]:
                    dut.errors += 1

        dut = DUTCore()
        dut.errors = 0
        run_simulation(dut, generator(dut), vcd_name='test.vcd')
        self.assertEqual(dut.errors, 0)

    def _test_axifull(self,
        naccesses=16, simultaneous_writes_reads=False,
        # Random: 0: min (no random), 100: max.
        # Burst randomness.
        id_rand_enable   = False,
        len_rand_enable  = False,
        data_rand_enable = False,
        # Flow valid randomness.
        aw_valid_random  = 0,
        w_valid_random   = 0,
        ar_valid_random  = 0,
        r_valid_random   = 0,
        # Flow ready randomness.
        w_ready_random   = 0,
        b_ready_random   = 0,
        r_ready_random   = 0,
        axi_dw           = 32, 
        axi_adrw         = 32,
        vcd_file         = None,
        ):

        def writes_cmd_generator(axi_port, writes):
            prng = random.Random(42)
            for write in writes:
                while prng.randrange(100) < aw_valid_random:
                    yield
                # Send command.
                yield axi_port.aw.valid.eq(1)
                offset = 2 if axi_dw == 32 else 3
                yield axi_port.aw.addr.eq(write.addr<<offset)
                yield axi_port.aw.burst.eq(write.type)
                yield axi_port.aw.len.eq(write.len)
                yield axi_port.aw.size.eq(write.size)
                yield axi_port.aw.id.eq(write.id)
                yield
                attempts = 0
                while (yield axi_port.aw.ready) == 0:
                    if attempts > 100:
                        print('writes_cmd_generator aw.ready')
                        return
                    attempts += 1
                    yield
                yield axi_port.aw.valid.eq(0)

        def writes_data_generator(axi_port, writes):
            prng = random.Random(42)
            yield axi_port.w.strb.eq(2**(len(axi_port.w.data)//8) - 1)
            for write in writes:
                for i, data in enumerate(write.data):
                    while prng.randrange(100) < w_valid_random:
                        yield
                    # Send data.
                    yield axi_port.w.valid.eq(1)
                    if (i == (len(write.data) - 1)):
                        yield axi_port.w.last.eq(1)
                    else:
                        yield axi_port.w.last.eq(0)
                    yield axi_port.w.data.eq(data)
                    yield
                    attempts = 0
                    while (yield axi_port.w.ready) == 0:
                        if attempts > 100:
                            print('writes_data_generator w.ready')
                            return
                        attempts += 1
                        yield
                    yield axi_port.w.valid.eq(0)
            axi_port.reads_enable = True

        def writes_response_generator(axi_port, writes):
            prng = random.Random(42)
            self.writes_id_errors = 0
            for write in writes:
                # wait response
                yield axi_port.b.ready.eq(0)
                yield
                attempts = 0
                while (yield axi_port.b.valid) == 0:
                    if attempts > 300*axi_dw//2:
                        self.writes_id_errors += 1
                        print('writes_response_generator b.valid')
                        return
                    attempts += 1
                    yield
                while prng.randrange(100) < b_ready_random:
                    yield
                yield axi_port.b.ready.eq(1)
                yield
                if (yield axi_port.b.id) != write.id:
                    self.writes_id_errors += 1

        def reads_cmd_generator(axi_port, reads):
            prng = random.Random(42)
            while not axi_port.reads_enable:
                yield
            for read in reads:
                while prng.randrange(100) < ar_valid_random:
                    yield
                # Send command.
                yield axi_port.ar.valid.eq(1)
                offset = 2 if axi_dw == 32 else 3
                yield axi_port.ar.addr.eq(read.addr<<offset)
                yield axi_port.ar.burst.eq(read.type)
                yield axi_port.ar.len.eq(read.len)
                yield axi_port.ar.size.eq(read.size)
                yield axi_port.ar.id.eq(read.id)
                yield
                attempts  = 0 
                while (yield axi_port.ar.ready) == 0:
                    if attempts > 100:
                        print('reads_cmd_generator ar.ready')
                        return
                    attempts += 1
                    yield
                yield axi_port.ar.valid.eq(0)

        def reads_response_data_generator(axi_port, reads):
            prng = random.Random(42)
            self.reads_data_errors = 0
            self.reads_id_errors   = 0
            self.reads_last_errors = 0
            while not axi_port.reads_enable:
                yield
            for read in reads:
                for i, data in enumerate(read.data):
                    # Wait data / response.
                    yield axi_port.r.ready.eq(0)
                    yield
                    attempts = 0
                    while (yield axi_port.r.valid) == 0:
                        if attempts > 100:
                            self.reads_data_errors += 1
                            self.reads_id_errors += 1
                            self.reads_last_errors += 1
                            print('reads_response_data_generator r.valid')
                            return
                        attempts += 1
                        yield
                    while prng.randrange(100) < r_ready_random:
                        yield
                    yield axi_port.r.ready.eq(1)
                    yield
                    if (yield axi_port.r.data) != data:
                        print('read error:', hex(read.addr<<3), hex((yield axi_port.r.data)), hex(data))
                        self.reads_data_errors += 1
                    if (yield axi_port.r.id) != read.id:
                        self.reads_id_errors += 1
                    if i == (len(read.data) - 1):
                        if (yield axi_port.r.last) != 1:
                            self.reads_last_errors += 1
                    else:
                        if (yield axi_port.r.last) != 0:
                            self.reads_last_errors += 1

        # dut = DUTAXIFull(axi_dw=axi_dw, axi_adrw=axi_adrw, test_ram_address=0x4000_0000)
        # dut = DUTAXI2AXILiteSimple(axi_dw=axi_dw, axi_adrw=axi_adrw)
        dut = DUTAXIFullCore(dw=axi_dw)

        # Generate writes/reads.
        prng   = random.Random(42)
        writes = []
        offset = 0
        for i in range(naccesses):
            _id   = prng.randrange(2**8) if id_rand_enable else i
            _len  = prng.randrange(32) if len_rand_enable else i
            _data = [prng.randrange(2**32) if data_rand_enable else j for j in range(_len + 1)]
            writes.append(Write(offset, _data, _id, type=BURST_INCR, len=_len, size=log2_int(axi_dw//8)))
            offset += _len + 1
        # Dummy reads to ensure datas have been written before the effective reads start.
        dummy_reads = [Read(1023, [0], 0, type=BURST_FIXED, len=0, size=log2_int(32//8)) for _ in range(32)]
        reads = writes

        # Simulation
        if simultaneous_writes_reads:
            dut.axi.reads_enable = True
        else:
            dut.axi.reads_enable = False # Will be set by writes_data_generator.
        generators = [
            writes_cmd_generator(dut.axi, writes),
            writes_data_generator(dut.axi, writes),
            writes_response_generator(dut.axi, writes),
            reads_cmd_generator(dut.axi, reads),
            reads_response_data_generator(dut.axi, reads),

        ]
        run_simulation(dut, generators, vcd_name=vcd_file)
        self.assertEqual(self.writes_id_errors,  0)
        self.assertEqual(self.reads_data_errors, 0)
        self.assertEqual(self.reads_id_errors,   0)
        self.assertEqual(self.reads_last_errors, 0)

    # Test with no randomness.
    def test_axi2wishbone_writes_then_reads_no_random(self):
        print('test_axi2wishbone_writes_then_reads_no_random')
        self._test_axifull(simultaneous_writes_reads=False, axi_dw=32)
        self._test_axifull(simultaneous_writes_reads=False, axi_dw=64)

    # Test with no randomness.
    def test_axi2wishbone_simple(self):
        print('test_axi2wishbone_simple')
        self._test_axifull(simultaneous_writes_reads=False, axi_dw=32)
        self._test_axifull(simultaneous_writes_reads=False, axi_dw=64)

    # Test randomness one parameter at a time.
    def test_axi2wishbone_writes_then_reads_random_bursts(self):
        print('test_axi2wishbone_writes_then_reads_random_bursts')
        self._test_axifull(
            simultaneous_writes_reads = False,
            id_rand_enable   = True,
            len_rand_enable  = True,
            data_rand_enable = True,
            axi_dw           = 32)
        self._test_axifull(
            simultaneous_writes_reads = False,
            id_rand_enable   = True,
            len_rand_enable  = True,
            data_rand_enable = True,
            axi_dw           = 64)

    def test_axi2wishbone_random_w_ready(self):
        print('test_axi2wishbone_random_w_ready')
        self._test_axifull(w_ready_random=90, axi_dw=32)
        self._test_axifull(w_ready_random=90, axi_dw=64)

    def test_axi2wishbone_random_b_ready(self):
        print('test_axi2wishbone_random_b_ready')
        self._test_axifull(b_ready_random=90, axi_dw=32)
        self._test_axifull(b_ready_random=90, axi_dw=64)

    @unittest.skip('hangs')
    def test_axi2wishbone_random_r_ready(self):
        print('test_axi2wishbone_random_r_ready')
        self._test_axifull(r_ready_random=90, axi_dw=32, vcd_file='full.vcd')
        # self._test_axifull(r_ready_random=90, axi_dw=64)

    def test_axi2wishbone_random_aw_valid(self):
        print('test_axi2wishbone_random_aw_valid')
        self._test_axifull(aw_valid_random=90, axi_dw=32)
        self._test_axifull(aw_valid_random=90, axi_dw=64)

    def test_axi2wishbone_random_w_valid(self):
        print('test_axi2wishbone_random_w_valid')
        self._test_axifull(w_valid_random=90, axi_dw=32)
        self._test_axifull(w_valid_random=90, axi_dw=64)

    def test_axi2wishbone_random_ar_valid(self):
        print('test_axi2wishbone_random_ar_valid')
        self._test_axifull(ar_valid_random=90, axi_dw=32)
        self._test_axifull(ar_valid_random=90, axi_dw=64)

    def test_axi2wishbone_random_r_valid(self):
        print('test_axi2wishbone_random_r_valid')
        self._test_axifull(r_valid_random=90, axi_dw=32)
        self._test_axifull(r_valid_random=90, axi_dw=64)

