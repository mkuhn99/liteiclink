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

class FakeOSERDESE2Impl(Module):
    def __init__(self, **kwargs):
        self.counter = counter = Signal(max=kwargs['p_DATA_WIDTH'])
        self.sync.sys += [
            self.counter.eq(0),
        ]
        self.sync.sys4x += [
            self.counter.eq(self.counter + 1)
        ]
        self.comb += [
            Case(counter,
                    {i:kwargs['o_OQ'].eq(kwargs[f'i_D{i+1}']) for i in range(kwargs['p_DATA_WIDTH'])}),
        ]

class FakeISERDESE2Impl(Module):
    def __init__(self, **kwargs):
        width = kwargs['p_DATA_WIDTH']
        self.data_internal = Signal(width)
        self.data_valid = Signal(width)
        self.counter = counter = Signal(max=kwargs['p_DATA_WIDTH'])
        self.counter_offset = counter_offset = Signal(max=kwargs['p_DATA_WIDTH'])
        self.sys_ = Signal()
        self.sync.sys += [
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
   

# Currently, delay is not simulated!
class FakeIDELAYE2Impl(Module):
    def __init__(self, **kwargs):
        self.comb += kwargs['o_DATAOUT'].eq(kwargs['i_IDATAIN'])

# Fake s7serdes Implementations -------------------------------------------------------------------------------------------

class FakeS7serdesInstance(Module):
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



# DUT System -----------------------------------------------------------------------------------------

class DUT(Module):
    def __init__(self, data_width=8, delay=0):
        self.o_OQ = Signal()
        self.i_DDLY = Signal()
        self.o_DATAOUT = Signal()
        self.i_IDATAIN = Signal()
        self.i_BITSLIP = Signal()
        self.counter_valid = Signal()
        self.kwargs = {
            'p_DATA_WIDTH': data_width,
            'o_OQ': self.o_OQ,
            'i_BITSLIP': self.i_BITSLIP,
            'i_DDLY': self.i_DDLY,
            'o_DATAOUT': self.o_DATAOUT,
            'i_IDATAIN': self.i_IDATAIN,
        }
        for i in range(data_width):
            self.kwargs[f'o_Q{i+1}'] = Signal()
            self.kwargs[f'i_D{i+1}'] = Signal()
        self.s7serdes_out = FakeOSERDESE2Impl(**self.kwargs)
        self.submodules += self.s7serdes_out
        self.s7serdes_in = FakeISERDESE2Impl(**self.kwargs)
        self.submodules += self.s7serdes_in
        
        if delay > 1:
            self.delayed = Signal(delay)
            self.sync.sys4x += [
                self.delayed.eq(Cat(self.delayed[-1], self.delayed[:-1])),
                self.delayed[0].eq(self.o_OQ),
                self.i_DDLY.eq(self.delayed[-1]),
            ]
        elif delay == 1:
            self.sync.sys4x += [
                self.i_DDLY.eq(self.o_OQ),
            ]
        else:
            self.comb += [
               self.i_DDLY.eq(self.o_OQ)
            ]

        self.data_in = Signal(data_width)
        self.data_out = Signal(data_width)
        self.data_out_valid = Signal(data_width)
        for i in range(data_width):
            self.comb += [self.data_out[data_width - 1 - i].eq(self.kwargs[f'o_Q{i+1}'])]
            self.comb += [self.kwargs[f'i_D{i+1}'].eq(self.data_in[i])]

    


class TestSERWBCore(unittest.TestCase):
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


    def test_serdes(self):
        def generator(dut):
            yield dut.i_BITSLIP.eq(1)
            for i in range( 8 - dut.offset):
                yield
            yield dut.i_BITSLIP.eq(0)
            yield
            # yield
            # yield
            # data = 0xa5
            # yield dut.data_in.eq(data)
            # while not (yield dut.data_out):
            #     yield
            # out = (yield dut.data_out_valid)
            # while not (yield dut.data_out_valid) == out:
            #     yield
            # out = (yield dut.data_out_valid)
            # if out != data:
            #     dut.errors += 1
            # data0 = 0x0
            # data1 = data
            out = 0x0
            for data in [0xa5, 0xb6, 0xc7, 0xd8, 0xe9, 0xfa]:
                yield dut.data_in.eq(data)
                if data == 0xa5:
                    yield
                while not (yield dut.data_out) != out:
                    yield
                out = (yield dut.data_out)
                if out != data:
                    dut.errors += 1

            if dut.errors:
                print(" ERROR: ", dut.errors)
            else:
                print(" SUCCESS")
        data_width = 8
        for delay in range(8):
            for offset in range(8):
                for phase in [5]:
                        print(f"{delay}; {offset}; {phase}: ")
                        dut = DUT(data_width=data_width, delay=delay)
                        dut.errors = 0
                        dut.offset = offset
                        base_clk = 10
                        serdes_rate = data_width
                        run_simulation(dut, generator(dut), special_overrides={DifferentialOutput: FakeDifferentialOutput,
                                                                            DifferentialInput: FakeDifferentialInput},
                                        clocks={'sys': (base_clk*serdes_rate, 8*phase), 'sys4x': (base_clk, phase)}, vcd_name=f'test_serdes_{delay}_{offset}_{phase}.vcd') #clocks={'sys': 8, 'sys4x':1},  , vcd_name = 't.vcd'
                    #self.assertEqual(dut.errors, 0)
