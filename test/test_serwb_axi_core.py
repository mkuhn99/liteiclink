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

from litex.soc.interconnect import stream

from liteiclink.serwb import scrambler
from liteiclink.serwb.core import SERWBCoreAXILite

from litex.soc.interconnect.axi import AXILiteSRAM

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
        phy_slaves = {k:FakePHY(dw=dw) for k, dw in {'aw':40, 'w':40, 'ar':40, 'r':40, 'b':32}.items()}
        serwb_slave = SERWBCoreAXILite(phy_slaves, int(1e6), mode="slave")
        self.submodules += serwb_slave


        # AXI master

        phy_masters = {k:FakePHY(dw=dw) for k, dw in {'aw':40, 'w':40, 'ar':40, 'r':40, 'b':32}.items()}
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
                yield from dut.axi.write((data_base + i*4), datas_w[i])

            # Read
            for i in range(data_length):
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
