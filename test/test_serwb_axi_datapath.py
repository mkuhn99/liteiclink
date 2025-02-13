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
from liteiclink.serwb.datapath import TXDatapath, RXDatapath
from litex.soc.interconnect.axi import AXILiteSRAM
CHANNELS_32BIT = ['aw', 'w', 'ctrl', 'r']
# Fake Init/Serdes/PHY -----------------------------------------------------------------------------

class FakeInit(LiteXModule):
    def __init__(self):
        self.ready = Signal(reset=1)


class FakeSerdes(LiteXModule):
    def __init__(self, dw):
        self.tx_dp = TXDatapath(8, packet_dw=dw)
        self.rx_dp = RXDatapath(8, packet_dw=dw)
        self.comb += [self.rx_dp.shift_inc.eq(0)]
        # self.tx_ce = Signal()
        # # self.tx_k  = Signal(4)
        # # self.tx_d  = Signal(dw)
        # self.rx_ce = Signal()
        # # self.rx_k  = Signal(4)
        # # self.rx_d  = Signal(dw)

        # # # #

        # data_ce = Signal(5, reset=0b00001)
        # self.sync += data_ce.eq(Cat(data_ce[1:], data_ce[0]))

        # self.comb += [
        #     self.tx_ce.eq(data_ce[0]),
        #     self.rx_ce.eq(data_ce[0])
        # ]

class FakePHY(LiteXModule):
    def __init__(self, dw:int):
        self.sink   = sink   = stream.Endpoint([("data", dw)])
        self.source = source = stream.Endpoint([("data", dw)])

        # # #

        self.init   = FakeInit()
        self.serdes = FakeSerdes(dw)
        self.first_invalid = Signal()
        # TX dataflow
        self.comb += [
            If(self.init.ready,
                sink.ready.eq(1),
                self.serdes.tx_dp.invalid.eq(1),
                If(sink.valid,
                    self.serdes.tx_dp.invalid.eq(0),
                    sink.connect(self.serdes.tx_dp.sink),
                    #self.serdes.tx_dp.sink.valid.eq(sink.valid)
                )
            )
        ]

        # RX dataflow
        self.comb += [

            source.valid.eq(~self.serdes.rx_dp.invalid & self.first_invalid & self.serdes.rx_dp.decoder.source.valid),
            If(self.init.ready,
                self.serdes.rx_dp.source.connect(source, omit={'valid'}),
            ),
        ]
        self.sync += [

            If(self.serdes.rx_dp.invalid & ~self.first_invalid,
               self.first_invalid.eq(1)
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
        phy_channels = {'aw':32, 'w':32, 'r':32, 'ctrl':32}
        # AXI slave
        self.slave_aw_phy = FakePHY(32)
        self.slave_w_phy = FakePHY(32)
        self.slave_r_phy = FakePHY(32)
        self.slave_ctrl_phy = FakePHY(32)
        self.phy_slaves = phy_slaves = {'aw':self.slave_aw_phy, 'w':self.slave_w_phy, 'r':self.slave_r_phy, 'ctrl':self.slave_ctrl_phy}
        serwb_slave = SERWBCoreAXILite(phy_slaves, int(1e6), mode="slave")
        self.submodules += serwb_slave


        # AXI master
        self.master_aw_phy = FakePHY(32)
        self.master_w_phy = FakePHY(32)
        self.master_r_phy = FakePHY(32)
        self.master_ctrl_phy = FakePHY(32)

        self.phy_masters = phy_masters = {'w':self.master_w_phy, 'aw':self.master_aw_phy, 'r':self.master_r_phy, 'ctrl':self.master_ctrl_phy}
        serwb_master = SERWBCoreAXILite(phy_masters, int(1e6), mode="master")
        self.submodules += serwb_master
        for k in CHANNELS_32BIT:
            self.submodules += phy_slaves[k], phy_masters[k]
            # Connect phy
            self.comb += [
                #phy_masters[k].serdes.rx_ce.eq(phy_slaves[k].serdes.tx_ce),
                #phy_masters[k].serdes.rx_dp.sink.data.eq(phy_slaves[k].serdes.tx_dp.source.data),
                phy_slaves[k].serdes.tx_dp.source.connect(phy_masters[k].serdes.rx_dp.sink, omit={'valid', 'ready'}),
                #phy_masters[k].serdes.rx_d.eq(phy_slaves[k].serdes.tx_d),

                #phy_slaves[k].serdes.rx_ce.eq(phy_masters[k].serdes.tx_ce),
                # phy_slaves[k].serdes.rx_dp.sink.data.eq(phy_masters[k].serdes.tx_dp.source.data),
                phy_masters[k].serdes.tx_dp.source.connect(phy_slaves[k].serdes.rx_dp.sink, omit={'valid', 'ready'}),
                #phy_slaves[k].serdes.rx_d.eq(phy_masters[k].serdes.tx_d)
            ]

        # Add AXI sram to AXI master
        sram = AXILiteSRAM(1024, bus=serwb_master.bus)
        self.submodules += sram

        # Expose AXI slave
        self.axi_slave = serwb_slave.bus
        self.axi_master = serwb_master.bus
        self.rx = RXDatapath(8)
        self.tx = TXDatapath(8)
        self.comb += [
            self.tx.source.connect(self.rx.sink, omit={'valid'}),
            #self.rx.sink.valid.eq(1),
            # self.rx.converter.source.valid.eq(1),
        ]

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
            # Init:

            yield dut.tx.invalid.eq(1)
            while not (yield dut.rx.sink.data == 0x00):
                yield
            yield
            for k in CHANNELS_32BIT:
                yield dut.rx.sink.valid.eq(1)
                yield dut.phy_masters[k].serdes.rx_dp.sink.valid.eq(1)
                yield dut.phy_slaves[k].serdes.rx_dp.sink.valid.eq(1)
                yield dut.phy_masters[k].serdes.rx_dp.sink.ready.eq(1)
                yield dut.phy_slaves[k].serdes.rx_dp.sink.ready.eq(1)
                yield dut.phy_masters[k].serdes.tx_dp.source.ready.eq(1)
                yield dut.phy_slaves[k].serdes.tx_dp.source.ready.eq(1)
            for k in CHANNELS_32BIT:
                yield dut.phy_masters[k].serdes.tx_dp.invalid.eq(1)
                yield dut.phy_slaves[k].serdes.tx_dp.invalid.eq(1)
            # Write
            for i in range(data_length):
                print(i)
                yield from dut.axi_slave.write((data_base + i*4), datas_w[i])

            # Read
            for i in range(data_length):
                datas_r.append((yield from dut.axi_slave.read((data_base + i*4)))[0])

            # Check
            print(datas_w)
            print(datas_r)
            for i in range(data_length):
                if datas_r[i] != datas_w[i]:
                    dut.errors += 1
            # # Write
            # for i in range(data_length):
            #     print(i)
            #     yield from dut.axi.write((data_base + i*4), datas_w[i])
            #     if (yield from dut.axi.read((data_base + i*4)))[0] != datas_w[i]:
            #         dut.errors += 1
            # while not (yield dut.rx.sink.data == 0xB9):
            #     yield
            # yield dut.rx.sink.valid.eq(1)
            # while not (yield dut.rx.invalid):
            #     yield
            
        dut = DUTCore()
        dut.errors = 0
        run_simulation(dut, generator(dut), vcd_name='test_axi_datapath.vcd')
        self.assertEqual(dut.errors, 0)
