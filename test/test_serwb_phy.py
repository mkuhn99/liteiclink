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
from litex.soc.interconnect.axi import AXILiteSRAM

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

# DUT System -----------------------------------------------------------------------------------------

class DUT(Module):
    def __init__(self):
        CHANNEL_DICT = {'aw':32, 'r':32, 'w':32, 'ctrl':32}
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
        self.master_ctrl_phy = SERWBPHY(device="xc7a", pads=self.master_pads[3], dw=32, mode="slave", init_timeout=2**to_e)

        self.master_phys = phy_masters = {'w':self.master_w_phy, 'aw':self.master_aw_phy, 'r':self.master_r_phy, 'ctrl':self.master_ctrl_phy}
        self.master_core = SERWBCoreAXILite(self.master_phys, mode='master', clk_freq=CLK, buffer_depth=0)
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
        self.slave_ctrl_phy = SERWBPHY(device="xc7a", pads=self.slave_pads[3], dw=32, mode="master", init_timeout=2**to_e)
        self.slave_phys = phy_slaves = {'aw':self.slave_aw_phy, 'w':self.slave_w_phy, 'r':self.slave_r_phy, 'ctrl':self.slave_ctrl_phy}
        self.slave_core = SERWBCoreAXILite(self.slave_phys, mode='slave', clk_freq=CLK, buffer_depth=0)
        self.submodules += self.slave_core
        delay = 2
        for i,k in enumerate(CHANNEL_DICT.keys()):
            self.submodules += self.master_phys[k], self.slave_phys[k]
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
        self.slave_axi = self.slave_core.bus

        
        sram = AXILiteSRAM(1024, bus=self.master_axi, init={0x5aa55aa5, 0x5aa55aa5, 0x5aa55aa5})
        self.submodules += sram


from pstats import Stats
import cProfile
class TestSERWBCore(unittest.TestCase):
    # Implementation Notes:
    # migen.fhdl.specials.Instance has no lower method -> Can't be replaced via special_overrides
    # Therefore use regular unittest class mocking

    def setUp(self):
        """init each test"""
        #self.pr = cProfile.Profile()
        #self.pr.enable()

    def tearDown(self):
        """finish any test"""
        #p = Stats (self.pr)
        #p.strip_dirs()
        #p.sort_stats ('cumtime')
        #p.print_stats ()
        print("\n--->>>")

    def test_serwb(self):
        def generator(dut):
            # Prepare test
            #prng        = random.Random(42)
            #data_base   = 0x100
            #data_length = 4
            #datas_w     = [prng.randrange(2**32) for i in range(data_length)]
            #datas_r     = []
            debug = True
            cycle = 0
            while not (yield dut.master_phys['ctrl'].init.ready) and not (yield dut.master_phys['ctrl'].init.error):
                yield
                cycle += 1
                if (cycle % 100) == 0:
                    print(f'init cycle: {cycle}', end='\r')
                if cycle == 4000:
                    print("No Sync!")
            # while not (yield dut.master_phys['r'].init.ready) and not (yield dut.master_phys['r'].init.error):
            #     yield
            if debug:
                print("s, delay_min_found:", (yield dut.slave_phys['ctrl'].init.delay_min_found))
                print("s, delay_min:", (yield dut.slave_phys['ctrl'].init.delay_min))
                print("s, delay_max_found:", (yield dut.slave_phys['ctrl'].init.delay_max_found))
                print("s, delay_max:", (yield dut.slave_phys['ctrl'].init.delay_max))
                print("s, delay:", (yield dut.slave_phys['ctrl'].init.delay))
                print("s, shift:", (yield dut.slave_phys['ctrl'].init.shift))
                print("s, error:", (yield dut.slave_phys['ctrl'].init.error))
                print("s, ready:", (yield dut.slave_phys['ctrl'].init.ready))
                print("m, delay_min_found:", (yield dut.master_phys['ctrl'].init.delay_min_found))
                print("m, delay_min:", (yield dut.master_phys['ctrl'].init.delay_min))
                print("m, delay_max_found:", (yield dut.master_phys['ctrl'].init.delay_max_found))
                print("m, delay_max:", (yield dut.master_phys['ctrl'].init.delay_max))
                print("m, delay:", (yield dut.master_phys['ctrl'].init.delay))
                print("m, shift:", (yield dut.master_phys['ctrl'].init.shift))
                print("m, error:", (yield dut.master_phys['ctrl'].init.error))
                print("m, ready:", (yield dut.master_phys['ctrl'].init.ready))
            # Write
            #for i in range(data_length):
            addr = 0x4
            data = 0x89abcdef
            r = (yield from dut.slave_axi.read(addr))
            if r != 0x5aa55aa5:
                dut.errors += 1
            yield from dut.slave_axi.write(addr, data)
            while not (yield dut.slave_axi.r.valid):
                yield
            for i in range(20):
                yield
            
            r = (yield from dut.slave_axi.read(addr))
            if r != data:
                dut.errors += 1
            # Read
            #for i in range(data_length):
            #    datas_r.append((yield from dut.wishbone.read(data_base + i)))

            # Check
            #for i in range(data_length):
            #    if datas_r[i] != datas_w[i]:
            #        dut.errors += 1

        
        #pads = Record([('clk_p',1), ('clk_n',1),
        #               ('rx_p', 1), ('rx_n', 1),
        #               ('tx_p', 1), ('tx_n', 1)])
        with patch('liteiclink.serwb.s7serdes.Instance', new=FakeS7serdesInstance) as fi: #migen.fhdl.specials.Instance
            with patch('liteiclink.serwb.phy._SerdesMasterInit', new=_SerdesMasterInitLowerTaps) as fi1: #migen.fhdl.specials.Instance
                with patch('liteiclink.serwb.phy._SerdesSlaveInit', new=_SerdesSlaveInitInitLowerTaps) as fi2: #migen.fhdl.specials.Instance
                    #fi.method.lower = instance_lower
                    #dut = SERWBPHY("xc7k", pads)
                    #dut.pads = pads
                    #dut.errors = 0
                    dut = DUT()
                    #print(Instance())
                    base_clk = 10
                    serdes_rate = 8
                    run_simulation(dut, generator(dut), special_overrides={DifferentialOutput: FakeDifferentialOutput,
                                                                           DifferentialInput: FakeDifferentialInput},
                                    clocks={'sys': (base_clk*serdes_rate, 40), 'sys4x': (base_clk, 5)}, vcd_name='test_serwb_phy.vcd') #clocks={'sys': 8, 'sys4x':1},  , vcd_name = 't.vcd'
                    self.assertEqual(dut.errors, 0)
