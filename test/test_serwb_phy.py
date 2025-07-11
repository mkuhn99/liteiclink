#!/usr/bin/env python3

#
# This file is part of LiteICLink.
#
# Copyright (c) 2023 Christian Klarhorst <cklarhor@techfak.uni-bielefeld.de>
# SPDX-License-Identifier: BSD-2-Clause

import unittest
import random

from migen import *

from litex.gen.sim import *

from litex.soc.interconnect import stream

from liteiclink.serwb import scrambler
from liteiclink.serwb.core import SERWBCoreAXILite

from litex.soc.interconnect.wishbone import SRAM
from litex.soc.interconnect.axi import AXILiteSRAM, AXILiteInterface, AXIInterface, AXI2AXILite
from litex.soc.interconnect.axi import *

from liteiclink.serwb.phy import SERWBPHY

from litex.build.io import DifferentialOutput, DifferentialInput

from migen.fhdl.specials import Instance
from unittest.mock import patch

# Low Level Fake Implementations ---------------------------------------------------------------------------------------

class FakeDifferentialOutputImpl(Module):
    def __init__(self, i, o_p, o_n):
        self.comb += [o_p.eq(i),
                      o_n.eq(~i)]

class FakeDifferentialOutput(DifferentialOutput):
    @staticmethod
    def lower(dr):
        return FakeDifferentialOutputImpl(dr.i, dr.o_p, dr.o_n)

class FakeDifferentialInputImpl(Module):
    def __init__(self, i_p, i_n, o):
        self.comb += [o.eq(i_p)]

class FakeDifferentialInput(DifferentialInput):
    @staticmethod
    def lower(dr):
        return FakeDifferentialInputImpl(dr.i_p, dr.i_n, dr.o)

# 7-Series Fake Implementations ----------------------------------------------------------------------------------------

class FakeISERDESE2Impl(Module):
    def __init__(self, **kwargs):
        width = kwargs['p_DATA_WIDTH']
        self.data_internal = Signal(width)
        self.data_valid = Signal(width)
        self.counter = counter = Signal(max=kwargs['p_DATA_WIDTH'])
        self.counter_offset = counter_offset = Signal(max=kwargs['p_DATA_WIDTH'])
        self.comb += self.counter_offset.eq(8)
        self.sys_ = Signal()
        self.sync += [
            # self.counter.eq(counter_offset),
            If(kwargs['i_BITSLIP'], counter_offset.eq(counter_offset-1)),
            self.sys_.eq(~self.sys_)
        ]
        self.rising_sys = Signal()
        self.last_sys = Signal()
        self.sync.sys4x += [
            Case(counter,
                    {i:self.data_internal[i].eq(kwargs['i_DDLY']) for i in range(kwargs['p_DATA_WIDTH'])}),
            If(counter==0,
                self.data_valid.eq(self.data_internal)
               ),
            self.last_sys.eq(self.sys_),
            self.rising_sys.eq(self.last_sys != self.sys_),
            If(self.rising_sys,
               self.counter.eq(self.counter_offset + 1)).Else(
                counter.eq(counter + 1)
               ),
        ]
        for i in range(width):
            self.sync += [kwargs[f'o_Q{width-i}'].eq(self.data_valid[i])]

class FakeOSERDESE2Impl(Module):
    def __init__(self, **kwargs):
        self.counter = counter = Signal(max=kwargs['p_DATA_WIDTH'])
        self.sync += [
            self.counter.eq(0),
        ]
        self.sync.sys4x += [
            self.counter.eq(self.counter + 1)
        ]
        self.comb += [
            Case(counter,
                    {i:kwargs['o_OQ'].eq(kwargs[f'i_D{i+1}']) for i in range(kwargs['p_DATA_WIDTH'])}),
        ]
        

# Currently, delay is not simulated!
class FakeIDELAYE2Impl(Module):
    def __init__(self, **kwargs):
        self.comb += kwargs['o_DATAOUT'].eq(kwargs['i_IDATAIN'])

# Fake s7serdes Implementations -------------------------------------------------------------------------------------------

class FakeS7serdesInstance(Instance):
    def __init__(self, of, *items, name="", synthesis_directive=None,
            attr=None, **kwargs):
        Instance.__init__(self, of, *items, name, synthesis_directive,
            attr, **kwargs)
        self.kwargs = kwargs

    @staticmethod
    def lower(dr):
        if dr.of == 'OSERDESE2':
            return FakeOSERDESE2Impl(**dr.kwargs)
        elif dr.of == 'ISERDESE2':
            return FakeISERDESE2Impl(**dr.kwargs)
        elif dr.of == 'IDELAYE2':
            return FakeIDELAYE2Impl(**dr.kwargs)

# Fake Init Funktions to Override Delay Tap-----------------------------------------------------------------------------
# This Unittest doesn't simulate the idelay component so we decrease the delay taps to save simulation time
from liteiclink.serwb.phy import _SerdesMasterInit, _SerdesSlaveInit
class _SerdesMasterInitLowerTaps(_SerdesMasterInit):
    def __init__(self, serdes, taps, timeout, clk_ratio):
        _SerdesMasterInit.__init__(self, serdes, 2, timeout, clk_ratio)

class _SerdesSlaveInitInitLowerTaps(_SerdesSlaveInit):
    def __init__(self, serdes, taps, timeout, clk_ratio):        
        _SerdesSlaveInit.__init__(self, serdes, 2, timeout, clk_ratio)

@ResetInserter()
class _SerdesMasterInitInstant(LiteXModule):
    def __init__(self, serdes, taps, timeout, clk_ratio="1:1"):
        self.ready = Signal()
        self.error = Signal()

        # # #

        self.delay           = delay           = Signal(max=taps)
        self.delay_min       = delay_min       = Signal(max=taps)
        self.delay_min_found = delay_min_found = Signal()
        self.delay_max       = delay_max       = Signal(max=taps)
        self.delay_max_found = delay_max_found = Signal()
        self.shift           = shift           = Signal(max=40)
        self.phase_sel       = phase_sel       = Signal(2)

        self.comb += [
            self.delay.eq(1),
            self.shift.eq(8),
        ]
@ResetInserter()
class _SerdesSlaveInitInstant(LiteXModule):
    def __init__(self, serdes, taps, timeout, clk_ratio="1:1"):
        self.ready = Signal()
        self.error = Signal()

        # # #

        self.delay           = delay           = Signal(max=taps)
        self.delay_min       = delay_min       = Signal(max=taps)
        self.delay_min_found = delay_min_found = Signal()
        self.delay_max       = delay_max       = Signal(max=taps)
        self.delay_max_found = delay_max_found = Signal()
        self.shift           = shift           = Signal(max=40)
        self.phase_sel       = phase_sel       = Signal(2)
        self.comb += [
            self.delay.eq(0),
            self.shift.eq(0),
        ]
# DUT System -----------------------------------------------------------------------------------------

class DUT(Module):
    def __init__(self, axi_dw=32):
        CLK = 8*10 #?
        to_e = 7
        self.master_pads = [Record([('clk_p',1), ('clk_n',1),
                                   ('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),]
        # note init_timeout master doesn't sync < 2**5 ?
        self.master_aw_phy = SERWBPHY(device="xc7a", pads=self.master_pads[0], dw=32, mode="slave", init_timeout=2**to_e)
        self.master_w_phy = SERWBPHY(device="xc7a", pads=self.master_pads[1], dw=32, mode="slave", init_timeout=2**to_e)
        self.master_r_phy = SERWBPHY(device="xc7a", pads=self.master_pads[2], dw=32, mode="slave", init_timeout=2**to_e)
        self.master_ar_phy = SERWBPHY(device="xc7a", pads=self.master_pads[3], dw=32, mode="slave", init_timeout=2**to_e)
        self.master_b_phy = SERWBPHY(device="xc7a", pads=self.master_pads[4], dw=32, mode="slave", init_timeout=2**to_e)


        self.master_phys = phy_masters = {'w':self.master_w_phy, 'aw':self.master_aw_phy, 'r':self.master_r_phy, 'ar':self.master_ar_phy, 'b':self.master_b_phy}
        self.master_core = SERWBCoreAXILite(self.master_phys, mode='master', clk_freq=CLK, axi_dw=axi_dw)
        self.submodules += self.master_core

        self.slave_pads = [Record([('clk_p',1), ('clk_n',1),
                                   ('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),]
        
        self.slave_aw_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[0], dw=32, mode="master", init_timeout=2**to_e)
        self.slave_w_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[1], dw=32, mode="master", init_timeout=2**to_e)
        self.slave_r_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[2], dw=32, mode="master", init_timeout=2**to_e)
        self.slave_ar_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[3], dw=32, mode="master", init_timeout=2**to_e)
        self.slave_b_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[4], dw=32, mode="master", init_timeout=2**to_e)

        self.slave_phys = phy_slaves = {'aw':self.slave_aw_phy, 'w':self.slave_w_phy, 'r':self.slave_r_phy, 'ar':self.slave_ar_phy, 'b':self.slave_b_phy}
        self.slave_core = SERWBCoreAXILite(self.slave_phys, mode='slave', clk_freq=CLK, axi_dw=axi_dw)
        self.submodules += self.slave_core
        delay = 2
        for i,k in enumerate(['aw', 'w', 'ar', 'r', 'b']):
            self.submodules += self.master_phys[k], self.slave_phys[k]
            # self.comb += [
            #     self.master_phys[k].serdes.rx.source.ready.eq(1),
            #     self.slave_phys[k].serdes.rx.source.ready.eq(1),
            # ]
            self.delayed_m = Signal(delay - 1)
            self.delayed_s = Signal(delay - 1)
            # self.sync.sys4x += [
            #     self.delayed_m.eq(Cat(self.delayed_m[-1], self.delayed_m[:-1])),
            #     self.delayed_s.eq(Cat(self.delayed_s[-1], self.delayed_s[:-1])),
            #     self.delayed_m[0].eq(self.slave_pads[i].tx_p),
            #     self.delayed_s[0].eq(self.master_pads[i].tx_p),
            #     self.master_pads[i].rx_p.eq(self.delayed_m[-1]),
            #     self.slave_pads[i].rx_p.eq(self.delayed_s[-1]),
            # ]
            self.sync.sys4x += [
                self.master_pads[i].rx_p.eq(self.slave_pads[i].tx_p),
                self.slave_pads[i].rx_p.eq(self.master_pads[i].tx_p)
            ]
        self.master_axi = self.master_core.bus
        self.axi_in = self.slave_core.bus

        
        sram = AXILiteSRAM(1024, bus=self.master_axi, init={0x5aa55aa5, 0x5aa55aa5, 0x5aa55aa5})
        self.submodules += sram


from pstats import Stats
import cProfile
# class TestSERWBCore(unittest.TestCase):
#     # Implementation Notes:
#     # migen.fhdl.specials.Instance has no lower method -> Can't be replaced via special_overrides
#     # Therefore use regular unittest class mocking

#     def setUp(self):
#         """init each test"""
#         #self.pr = cProfile.Profile()
#         #self.pr.enable()

#     def tearDown(self):
#         """finish any test"""
#         #p = Stats (self.pr)
#         #p.strip_dirs()
#         #p.sort_stats ('cumtime')
#         #p.print_stats ()
#         print("\n--->>>")

#     def test_serwb(self):
#         def generator(dut):
#             # Prepare test
#             #prng        = random.Random(42)
#             #data_base   = 0x100
#             #data_length = 4
#             #datas_w     = [prng.randrange(2**32) for i in range(data_length)]
#             #datas_r     = []
#             debug = True
#             cycle = 0
#             while not (yield dut.master_phys['aw'].init.ready) and not (yield dut.master_phys['aw'].init.error):
#                 yield
#                 cycle += 1
#                 if (cycle % 100) == 0:
#                     print(f'init cycle: {cycle}', end='\r')
#                 if cycle == 4000:
#                     print("No Sync!")
#             for c in ['aw', 'w', 'ar', 'r', 'b']:
#                 print(f"{c}: init {(yield dut.master_phys[c].init.ready)} error {(yield dut.master_phys[c].init.error)}")
#             # while not (yield dut.master_phys['r'].init.ready) and not (yield dut.master_phys['r'].init.error):
#             #     yield
#             if debug:
#                 print("s, delay_min_found:", (yield dut.slave_phys['aw'].init.delay_min_found))
#                 print("s, delay_min:", (yield dut.slave_phys['aw'].init.delay_min))
#                 print("s, delay_max_found:", (yield dut.slave_phys['aw'].init.delay_max_found))
#                 print("s, delay_max:", (yield dut.slave_phys['aw'].init.delay_max))
#                 print("s, delay:", (yield dut.slave_phys['aw'].init.delay))
#                 print("s, shift:", (yield dut.slave_phys['aw'].init.shift))
#                 print("s, error:", (yield dut.slave_phys['aw'].init.error))
#                 print("s, ready:", (yield dut.slave_phys['aw'].init.ready))
#                 print("m, delay_min_found:", (yield dut.master_phys['aw'].init.delay_min_found))
#                 print("m, delay_min:", (yield dut.master_phys['aw'].init.delay_min))
#                 print("m, delay_max_found:", (yield dut.master_phys['aw'].init.delay_max_found))
#                 print("m, delay_max:", (yield dut.master_phys['aw'].init.delay_max))
#                 print("m, delay:", (yield dut.master_phys['aw'].init.delay))
#                 print("m, shift:", (yield dut.master_phys['aw'].init.shift))
#                 print("m, error:", (yield dut.master_phys['aw'].init.error))
#                 print("m, ready:", (yield dut.master_phys['aw'].init.ready))
            
#             # Prepare test
#             prng        = random.Random(42)
#             data_base   = 0x0
#             data_length = 8
#             datas_w     = [prng.randrange(2**dut.axi_dw) for i in range(data_length)]
#             datas_r     = []
#             # Write
#             for i in range(data_length):
#                 print("Write %d" % i, end='\r')
#                 yield from dut.axi_in.write((data_base + i*(dut.axi_dw//8)), datas_w[i])

#             # Read
#             for i in range(data_length):
#                 print("Read %d" % i, end='\r')
#                 datas_r.append((yield from dut.axi_in.read((data_base + i*(dut.axi_dw//8))))[0])

#             # Check
#             for i in range(data_length):
#                 if datas_r[i] != datas_w[i]:
#                     dut.errors += 1
#             datas_w     = [prng.randrange(2**dut.axi_dw) for i in range(data_length)]
#             datas_r     = []
#             # Alternate Read & Write
#             for i in range(data_length):
#                 print("Alternate %d" % i, end='\r')
#                 yield from dut.axi_in.write((data_base + i*(dut.axi_dw//8)), datas_w[i])
#                 datas_r.append((yield from dut.axi_in.read((data_base + i*(dut.axi_dw//8))))[0])

#             # Check
#             for i in range(data_length):
#                 if datas_r[i] != datas_w[i]:
#                     dut.errors += 1


#         with patch('liteiclink.serwb.s7serdes.Instance', new=FakeS7serdesInstance) as fi: #migen.fhdl.specials.Instance
#             with patch('liteiclink.serwb.phy._SerdesMasterInit', new=_SerdesMasterInitLowerTaps) as fi1: #migen.fhdl.specials.Instance
#                 with patch('liteiclink.serwb.phy._SerdesSlaveInit', new=_SerdesSlaveInitInitLowerTaps) as fi2: #migen.fhdl.specials.Instance
#                     dut = DUT(axi_dw=64)
#                     dut.errors = 0
#                     dut.axi_dw = 64
#                     base_clk = 10
#                     serdes_rate = 8
#                     run_simulation(dut, generator(dut), special_overrides={DifferentialOutput: FakeDifferentialOutput,
#                                                                            DifferentialInput: FakeDifferentialInput},
#                                     clocks={'sys': (base_clk*serdes_rate, 40), 'sys4x': (base_clk, 5)}, vcd_name='phy.vcd') #clocks={'sys': 8, 'sys4x':1},  , vcd_name = 't.vcd'
#                     self.assertEqual(dut.errors, 0)

# DUT System -----------------------------------------------------------------------------------------

class DUTAxiFull(Module):
    def __init__(self, axi_dw=32):
        CLK = 8*10 #?
        to_e = 7

        self.axi_out = AXIInterface(data_width=axi_dw, id_width=8)
        self.axi = AXIInterface(data_width=axi_dw, id_width=8)

        self.master_pads = [Record([('clk_p',1), ('clk_n',1),
                                   ('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),]
        # note init_timeout master doesn't sync < 2**5 ?
        self.master_aw_phy = SERWBPHY(device="xc7a", pads=self.master_pads[0], dw=32, mode="slave", init_timeout=2**to_e)
        self.master_w_phy = SERWBPHY(device="xc7a", pads=self.master_pads[1], dw=32, mode="slave", init_timeout=2**to_e)
        self.master_r_phy = SERWBPHY(device="xc7a", pads=self.master_pads[2], dw=32, mode="slave", init_timeout=2**to_e)
        self.master_ar_phy = SERWBPHY(device="xc7a", pads=self.master_pads[3], dw=32, mode="slave", init_timeout=2**to_e)
        self.master_b_phy = SERWBPHY(device="xc7a", pads=self.master_pads[4], dw=32, mode="slave", init_timeout=2**to_e)


        self.master_phys = phy_masters = {'w':self.master_w_phy, 'aw':self.master_aw_phy, 'r':self.master_r_phy, 'ar':self.master_ar_phy, 'b':self.master_b_phy}
        self.master_core = SERWBCoreAXILite(self.master_phys, mode='master', clk_freq=CLK, axi_interface=self.axi_out)
        self.submodules += self.master_core

        self.slave_pads = [Record([('clk_p',1), ('clk_n',1),
                                   ('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),
                            Record([('rx_p', 1), ('rx_n', 1),
                                   ('tx_p', 1), ('tx_n', 1)]),]
        
        self.slave_aw_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[0], dw=32, mode="master", init_timeout=2**to_e)
        self.slave_w_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[1], dw=32, mode="master", init_timeout=2**to_e)
        self.slave_r_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[2], dw=32, mode="master", init_timeout=2**to_e)
        self.slave_ar_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[3], dw=32, mode="master", init_timeout=2**to_e)
        self.slave_b_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[4], dw=32, mode="master", init_timeout=2**to_e)

        self.slave_phys = phy_slaves = {'aw':self.slave_aw_phy, 'w':self.slave_w_phy, 'r':self.slave_r_phy, 'ar':self.slave_ar_phy, 'b':self.slave_b_phy}
        self.slave_core = SERWBCoreAXILite(self.slave_phys, mode='slave', clk_freq=CLK, axi_interface=self.axi)
        self.submodules += self.slave_core
        delay = 2
        for i,k in enumerate(['aw', 'w', 'ar', 'r', 'b']):
            self.submodules += self.master_phys[k], self.slave_phys[k]
            # self.comb += [
            #     self.master_phys[k].serdes.rx.source.ready.eq(1),
            #     self.slave_phys[k].serdes.rx.source.ready.eq(1),
            # ]
            self.delayed_m = Signal(delay - 1)
            self.delayed_s = Signal(delay - 1)
            # self.sync.sys4x += [
            #     self.delayed_m.eq(Cat(self.delayed_m[-1], self.delayed_m[:-1])),
            #     self.delayed_s.eq(Cat(self.delayed_s[-1], self.delayed_s[:-1])),
            #     self.delayed_m[0].eq(self.slave_pads[i].tx_p),
            #     self.delayed_s[0].eq(self.master_pads[i].tx_p),
            #     self.master_pads[i].rx_p.eq(self.delayed_m[-1]),
            #     self.slave_pads[i].rx_p.eq(self.delayed_s[-1]),
            # ]
            self.sync.sys4x += [
                self.master_pads[i].rx_p.eq(self.slave_pads[i].tx_p),
                self.slave_pads[i].rx_p.eq(self.master_pads[i].tx_p)
            ]

        self.ram_bus = AXILiteInterface(data_width=axi_dw)
        self.axi2axilite = AXI2AXILite(self.axi_out, self.ram_bus)
        self.submodules += self.axi2axilite
        sram = AXILiteSRAM(1024*2, bus=self.ram_bus, init={0x5aa55aa5, 0x5aa55aa5, 0x5aa55aa5})
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

class TestSERWBCoreFull(unittest.TestCase):
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
        def connect(dut):
            debug = True
            for _ in range(9):
                for k, phy in dut.master_phys.items():
                    yield phy.serdes.rx.shift_inc.eq(1)
                    yield phy.serdes.rx.datapath.shift_inc.eq(1)
                for k, phy in dut.slave_phys.items():
                    yield phy.serdes.rx.shift_inc.eq(1)
                    yield phy.serdes.rx.datapath.shift_inc.eq(1)
                yield
                
            for k, phy in dut.master_phys.items():
                yield phy.serdes.rx.shift_inc.eq(0)
                yield phy.serdes.rx.datapath.shift_inc.eq(0)
                yield phy.init.ready.eq(1)
            # yield
            for k, phy in dut.slave_phys.items():
                yield phy.serdes.rx.shift_inc.eq(0)
                yield phy.serdes.rx.datapath.shift_inc.eq(0)
                yield phy.init.ready.eq(1)
            yield
            dut.axi.init = True

        def writes_cmd_generator(axi_port, writes):
            prng = random.Random(42)
            while not axi_port.init:
                yield
            print('Writes_CMD')
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
                print("AW DONE")

        def writes_data_generator(axi_port, writes):
            prng = random.Random(42)
            print('Writes_Data')
            while not axi_port.init:
                yield
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
                    print('w done')
            axi_port.reads_enable = True

        def writes_response_generator(axi_port, writes):
            print('Writes_response')
            prng = random.Random(42)
            self.writes_id_errors = 0
            while not axi_port.init:
                yield
            for write in writes:
                # wait response
                yield axi_port.b.ready.eq(0)
                yield
                attempts = 0
                while (yield axi_port.b.valid) == 0:
                    if attempts > 600*axi_dw//32:
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
                print('b done')

        def reads_cmd_generator(axi_port, reads):
            print('Reads_CMD')
            prng = random.Random(42)

            while not axi_port.init:
                yield
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
                print("AR DONE")

        def reads_response_data_generator(axi_port, reads):

            print('reads_response')
            prng = random.Random(42)
            self.reads_data_errors = 0
            self.reads_id_errors   = 0
            self.reads_last_errors = 0

            while not axi_port.init:
                yield
            while not axi_port.reads_enable:
                yield
            for read in reads:
                for i, data in enumerate(read.data):
                    # Wait data / response.
                    yield axi_port.r.ready.eq(0)
                    yield
                    attempts = 0
                    while (yield axi_port.r.valid) == 0:
                        if attempts > 200:
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

        with patch('liteiclink.serwb.s7serdes.Instance', new=FakeS7serdesInstance) as fi: #migen.fhdl.specials.Instance
            with patch('liteiclink.serwb.phy._SerdesMasterInit', new=_SerdesMasterInitInstant) as fi1: #migen.fhdl.specials.Instance
                with patch('liteiclink.serwb.phy._SerdesSlaveInit', new=_SerdesSlaveInitInstant) as fi2: #migen.fhdl.specials.Instance
                    dut = DUTAxiFull(axi_dw=axi_dw)

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
                    dut.axi.init = False
                    generators = [
                        connect(dut),
                        writes_cmd_generator(dut.axi, writes),
                        writes_data_generator(dut.axi, writes),
                        writes_response_generator(dut.axi, writes),
                        reads_cmd_generator(dut.axi, reads),
                        reads_response_data_generator(dut.axi, reads),

                    ]
                    base_clk = 10
                    serdes_rate = 8
                    run_simulation(dut, generators, vcd_name=vcd_file, special_overrides={DifferentialOutput: FakeDifferentialOutput,
                                                                           DifferentialInput: FakeDifferentialInput},
                                    clocks={'sys': (base_clk*serdes_rate, 40), 'sys4x': (base_clk, 5)})
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

