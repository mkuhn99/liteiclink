import unittest
import random

from migen import *

from litex.gen.sim import *

class FakeISERDESE2Impl(Module):
    def __init__(self, **kwargs):
        width = kwargs['p_DATA_WIDTH']
        self.data_internal = Signal(width)
        self.data_valid = Signal(width)
        self.counter = counter = Signal(max=kwargs['p_DATA_WIDTH'])
        self.counter_offset = counter_offset = Signal(max=kwargs['p_DATA_WIDTH'])
        self.sys_ = Signal()
        self.sync.sys1_4x += [
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
            self.sync.sys1_4x += [kwargs[f'o_Q{width-i}'].eq(self.data_valid[i])]

class FakeOSERDESE2Impl(Module):
    def __init__(self, **kwargs):
        self.counter = counter = Signal(max=kwargs['p_DATA_WIDTH'])
        self.sync.sys1_4x += [
            self.counter.eq(0),
        ]
        self.sync.sys4x += [
            self.counter.eq(self.counter + 1)
        ]
        self.comb += [
            Case(counter,
                    {i:kwargs['o_OQ'].eq(kwargs[f'i_D{i+1}']) for i in range(kwargs['p_DATA_WIDTH'])}),
        ]

class DUT_OSERDESE2(Module):
    def __init__(self, data_width=8):
        self.o_OQ = Signal()
        self.kwargs = {'o_OQ':self.o_OQ, 'p_DATA_WIDTH':data_width}
        self.data_in = Signal(data_width)
        for i in range(data_width):
            self.kwargs[f'i_D{i+1}'] = Signal()
            self.comb += [self.kwargs[f'i_D{i+1}'].eq(self.data_in[i])]
        self.oserdese2 = FakeOSERDESE2Impl(**self.kwargs)
        self.submodules += self.oserdese2

class DUT_ISERDESE2(Module):
    def __init__(self, data_width=8):
        self.i_DDLY = Signal()
        self.i_BITSLIP = Signal()
        self.kwargs = {'i_DDLY':self.i_DDLY, 'p_DATA_WIDTH':data_width, 'i_BITSLIP':self.i_BITSLIP}
        self.data_out = Signal(data_width)
        for i in range(data_width):
            self.kwargs[f'o_Q{i+1}'] = Signal()
        for i in range(data_width):
            self.comb += [self.data_out[i].eq(self.kwargs[f'o_Q{data_width - i}'])]
        self.iserdese2 = FakeISERDESE2Impl(**self.kwargs)
        self.submodules += self.iserdese2

class TestSERWBCore(unittest.TestCase):

    def test_oserdese2(self):
        data_width = 8
        base_clk = 10
        serdes_rate = 8
        phase = 5
        data = [0x1, 2**7, 2**8 - 1, 0xa5]
        prng        = random.Random(42)
        data    = [prng.randrange(2**serdes_rate) for i in range(1000)]
        def dut_test(dut):
            for i in range(serdes_rate - 1):
                yield
            for d in data:
                yield dut.data_in.eq(d)
                for i in range(data_width):
                    yield
                    o = (yield dut.o_OQ)
                    if o != ((d>>i)&1):
                       dut.errors += 1
        dut = DUT_OSERDESE2(data_width)
        dut.errors = 0
        run_simulation( dut, 
                        dut_test(dut),
                        clocks={'sys4x': (base_clk, phase), 'sys1_4x': (base_clk*serdes_rate, 8*phase), 'sys': (base_clk, phase)},
                        vcd_name='test_oserdes2impl.vcd')
        self.assertEqual(dut.errors, 0)

    def test_iserdese2(self):
        data_width = 8
        base_clk = 10
        serdes_rate = 8
        phase = 5
        data = [0x1, 2**7, 2**8 - 1, 0xa5]
        prng        = random.Random(42)
        data    = [[prng.randrange(2) for i in range(serdes_rate)] for i in range(100)]

        def dut_test(dut):
            out_ = []
            for i in range(serdes_rate - 1):
                yield
            for d in data:
                for i in range(data_width):
                    yield
                    yield dut.i_DDLY.eq(d[i])
                
                o = (yield dut.data_out)
                out_ += [o]
            for j in range(3):
                for i in range(serdes_rate - 1):
                    yield
                o = (yield dut.data_out)
                out_ += [o]
            j = 0
            for o in out_:
                if o==0:
                    j += 1
                else:
                    break
            for i, d in enumerate(data):
                if sum([d[i]*2**i for i in range(serdes_rate)]) != out_[i+j]:
                    dut.errors += 1
            
        dut = DUT_ISERDESE2(data_width)
        dut.errors = 0
        run_simulation( dut, 
                        dut_test(dut),
                        clocks={'sys4x': (base_clk, phase), 'sys1_4x': (base_clk*serdes_rate, 8*phase), 'sys': (base_clk, phase)},
                        vcd_name='test_iserdes2impl.vcd')
        self.assertEqual(dut.errors, 0)