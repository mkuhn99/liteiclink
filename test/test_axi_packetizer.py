import unittest
import random

from migen import *

from litex.gen import *

from litex.gen.sim import *

from litex.soc.interconnect import stream
from litex.soc.interconnect.axi import AXILiteSRAM, AXILiteInterface

from liteiclink.serwb.packet import AxiPacketizer, AxiDepacketizer
from litex.soc.interconnect.axi import AXILiteSRAM, AXILiteInterface
MEM = {0x89abcdef, 0xf891bcde, 0xef89abcd, 0xdef89abc, 0xcdef89ab, 0xbcdef89a, 0xabcdef89, 0x9abcdef8}
class DUTConverter(LiteXModule):
    def __init__(self):
        self.in_stream = stream.Endpoint([('d1', 32), ('d2', 4), ('pad', 28)])
        self.cast_in = stream.Cast(self.in_stream.description, [('data', 64)])
        self.converter1 = stream.Converter(64, 32)
        self.converter2 = stream.Converter(32, 64)
        self.out_stream = stream.Endpoint([('d1',32), ('d2',4), ('pad', 28)])
        self.cast_out = stream.Cast([('data', 64)], self.out_stream.description)
        self.comb += [
            self.in_stream.connect(self.cast_in.sink),
            self.cast_in.source.connect(self.converter1.sink),
            self.converter1.source.connect(self.converter2.sink),
            self.converter2.source.connect(self.cast_out.sink),
            self.cast_out.source.connect(self.out_stream),
        ]

class DUTSimple(LiteXModule):
    def __init__(self, **kwargs):
        self.in_stream = stream.Endpoint([('d1', 32), ('d2', 4)])
        self.packetizer = AxiPacketizer(axi_endpoint=self.in_stream)
        self.out_stream = stream.Endpoint([('d1',32), ('d2',4)])
        self.depacketizer = AxiDepacketizer(axi_endpoint=self.out_stream, clk_freq=3)

        self.comb += [
            self.packetizer.source.connect(self.depacketizer.sink),
        ]

class DUTAXI(LiteXModule):
    def __init__(self, axi_dw=32, axi_adrw=32, **kwargs):
        self.axi_dw = axi_dw
        self.axi_adrw = axi_adrw
        self.axi_in = AXILiteInterface(data_width=self.axi_dw, address_width=self.axi_adrw)
        self.axi_out = AXILiteInterface(data_width=self.axi_dw, address_width=self.axi_adrw)

        self.ar_packetizer = AxiPacketizer(axi_endpoint=self.axi_in.ar)
        self.aw_packetizer = AxiPacketizer(axi_endpoint=self.axi_in.aw)
        self.w_packetizer = AxiPacketizer(axi_endpoint=self.axi_in.w)
        self.b_depacketizer = AxiDepacketizer(axi_endpoint=self.axi_in.b, clk_freq=int(1e6))
        self.r_depacketizer = AxiDepacketizer(axi_endpoint=self.axi_in.r, clk_freq=int(1e6))


        self.aw_depacketizer = AxiDepacketizer(axi_endpoint=self.axi_out.aw, clk_freq=int(1e6))
        self.ar_depacketizer = AxiDepacketizer(axi_endpoint=self.axi_out.ar, clk_freq=int(1e6))
        self.w_depacketizer = AxiDepacketizer(axi_endpoint=self.axi_out.w, clk_freq=int(1e6))
        self.b_packetizer = AxiPacketizer(axi_endpoint=self.axi_out.b)
        self.r_packetizer = AxiPacketizer(axi_endpoint=self.axi_out.r)
        self.comb += [
            self.aw_packetizer.source.connect(self.aw_depacketizer.sink),
            self.ar_packetizer.source.connect(self.ar_depacketizer.sink),
            self.w_packetizer.source.connect(self.w_depacketizer.sink),
            self.b_packetizer.source.connect(self.b_depacketizer.sink),
            self.r_packetizer.source.connect(self.r_depacketizer.sink),
        ]

        self.sram = AXILiteSRAM(11*1024, bus=self.axi_out, init={0x89abcdef, 0xf891bcde, 0xef89abcd, 0xdef89abc, 0xcdef89ab, 0xbcdef89a, 0xabcdef89, 0x9abcdef8})
        self.submodules += self.sram

# Test ----------------------------------------------------------------------------------

class Test(unittest.TestCase):
    def test_converter(self):
        def generator(dut):
            # Prepare test
            prng        = random.Random(42)
            data_base   = 0x4
            data_length = 5
            datas_w     = [(prng.randrange(2**32), prng.randrange(2**4)) for i in range(data_length)]
            datas_r     = []
            for d in datas_w:
                d1, d2 = d
                yield dut.in_stream.d1.eq(d1)
                yield dut.in_stream.d2.eq(d2)
                yield dut.in_stream.valid.eq(1)
                while not (yield dut.in_stream.ready):
                    yield
                yield dut.in_stream.valid.eq(0)
                while not (yield dut.out_stream.valid):
                    yield
                yield
                d1_o = (yield dut.out_stream.d1)
                d2_o = (yield dut.out_stream.d2)
                yield dut.out_stream.ready.eq(1)
                yield
                yield dut.out_stream.ready.eq(0)
                
                if d1 != d1_o or d2 != d2_o:
                    dut.errors += 1

            
        dut = DUTConverter()
        dut.errors = 0
        run_simulation(dut, generator(dut), vcd_name='test_packetizer_converter.vcd')
        self.assertEqual(dut.errors, 0)

    def test_simple(self):
        def generator(dut):
            # Prepare test
            prng        = random.Random(42)
            data_base   = 0x4
            data_length = 5
            datas_w     = [(prng.randrange(2**32), prng.randrange(2**4)) for i in range(data_length)]
            datas_r     = []
            for d in datas_w:
                d1, d2 = d
                yield dut.in_stream.d1.eq(d1)
                yield dut.in_stream.d2.eq(d2)
                yield dut.in_stream.valid.eq(1)
                while not (yield dut.in_stream.ready):
                    yield
                yield dut.in_stream.valid.eq(0)
                while not (yield dut.out_stream.valid):
                    yield
                yield
                d1_o = (yield dut.out_stream.d1)
                d2_o = (yield dut.out_stream.d2)
                yield dut.out_stream.ready.eq(1)
                yield
                yield dut.out_stream.ready.eq(0)
                
                if d1 != d1_o or d2 != d2_o:
                    dut.errors += 1

            
        dut = DUTSimple()
        dut.errors = 0
        run_simulation(dut, generator(dut), vcd_name='test_packetizer_simple.vcd')
        self.assertEqual(dut.errors, 0)
    
    def test_serwb(self):
        def generator(dut):
            # Prepare test
            prng        = random.Random(42)
            data_base   = 0x0
            data_length = 8
            datas_w     = [prng.randrange(2**dut.axi_dw) for i in range(data_length)]
            datas_r     = []
            
            # Write
            for i in range(data_length):
                yield from dut.axi_in.write((data_base + i*(dut.axi_dw//8)), datas_w[i])

            # Read
            for i in range(data_length):
                datas_r.append((yield from dut.axi_in.read((data_base + i*(dut.axi_dw//8))))[0])

            # Check
            for i in range(data_length):
                if datas_r[i] != datas_w[i]:
                    dut.errors += 1
            datas_w     = [prng.randrange(2**dut.axi_dw) for i in range(data_length)]
            datas_r     = []
            # Alternate Read & Write
            for i in range(data_length):
                yield from dut.axi_in.write((data_base + i*(dut.axi_dw//8)), datas_w[i])
                datas_r.append((yield from dut.axi_in.read((data_base + i*(dut.axi_dw//8))))[0])

            # Check
            for i in range(data_length):
                if datas_r[i] != datas_w[i]:
                    dut.errors += 1
        acc_errors = 0
        for dw in [32, 64, 128, 512]:
                axi_adrw = dw
                axi_dw = dw
                dut = DUTAXI(axi_dw=axi_dw, axi_adrw=axi_adrw)
                dut.errors = 0
                run_simulation(dut, generator(dut), vcd_name=f'test_axipacketizer_dw{axi_dw}_adrw{axi_adrw}.vcd')
                print(f"axi_dw:{axi_dw}  axi_adrw:{axi_adrw}: {dut.errors==0}")
                acc_errors += dut.errors


                axi_adrw = 32
                axi_dw = dw
                dut = DUTAXI(axi_dw=axi_dw, axi_adrw=axi_adrw)
                dut.errors = 0
                run_simulation(dut, generator(dut), vcd_name=f'test_axipacketizer_dw{axi_dw}_adrw{axi_adrw}.vcd')
                print(f"axi_dw:{axi_dw}  axi_adrw:{axi_adrw}: {dut.errors==0}")
                acc_errors += dut.errors
        self.assertEqual(acc_errors, 0)
