import unittest
import random

from migen import *

from litex.gen import *

from litex.gen.sim import *

from litex.soc.interconnect import stream
from litex.soc.interconnect.axi import *

from liteiclink.serwb.packet import AxiPacketizer, AxiDepacketizer
from litex.soc.interconnect.axi import AXILiteSRAM, AXILiteInterface, connect_axi
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
    def __init__(self, axi_dw=32, axi_adrw=32, test_ram_address=0x4000_0000, **kwargs):
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
        self.ram_bus = ram_bus =  AXILiteInterface(data_width=self.axi_dw, address_width=self.axi_adrw)

        self.comb += connect_axi(self.axi_out, ram_bus, omit={'addr'})
        self.comb += [
            ram_bus.ar.addr.eq(self.axi_out.ar.addr & (test_ram_address - 1)),
            ram_bus.aw.addr.eq(self.axi_out.aw.addr & (test_ram_address - 1)),
            ]
        self.sram = AXILiteSRAM(11*1024, bus=self.axi_out, init={0x89abcdef, 0xf891bcde, 0xef89abcd, 0xdef89abc, 0xcdef89ab, 0xbcdef89a, 0xabcdef89, 0x9abcdef8})
        self.submodules += self.sram

class DUTOnlyAXIFull(LiteXModule):
    def __init__(self, axi_dw=32, axi_adrw=32, test_ram_address=0x4000_0000, **kwargs):
        self.axi_dw = axi_dw
        self.axi_adrw = axi_adrw
        self.axi = AXIInterface(data_width=self.axi_dw, address_width=self.axi_adrw, id_width=8)
        self.axi_out = AXIInterface(data_width=self.axi_dw, address_width=self.axi_adrw, id_width=8)

        self.ar_packetizer = AxiPacketizer(axi_endpoint=self.axi.ar)
        self.aw_packetizer = AxiPacketizer(axi_endpoint=self.axi.aw)
        self.w_packetizer = AxiPacketizer(axi_endpoint=self.axi.w)
        self.b_depacketizer = AxiDepacketizer(axi_endpoint=self.axi.b, clk_freq=int(1e6))
        self.r_depacketizer = AxiDepacketizer(axi_endpoint=self.axi.r, clk_freq=int(1e6))


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
        self.ram_bus = ram_bus =  AXILiteInterface(data_width=self.axi_dw, address_width=self.axi_adrw)

        self.sram = AXILiteSRAM(11*1024, bus=ram_bus, init={0x89abcdef, 0xf891bcde, 0xef89abcd, 0xdef89abc, 0xcdef89ab, 0xbcdef89a, 0xabcdef89, 0x9abcdef8})
        self.submodules += self.sram

        self.axi2axilite = AXI2AXILite(self.axi_out, self.ram_bus)# self.serwb_master_core.bus)
        self.submodules += self.axi2axilite
        # self.comb += [self.axi_out.b.ready.eq(1)]


class DUTAXIFull(LiteXModule):
    def __init__(self, axi_dw=32, axi_adrw=32, test_ram_address=0x4000_0000, **kwargs):
        self.axi_dw = axi_dw
        self.axi_adrw = axi_adrw
        self.axi = AXIInterface(data_width=self.axi_dw, address_width=self.axi_adrw, id_width=8)
        self.axi_in = AXILiteInterface(data_width=self.axi_dw, address_width=self.axi_adrw)
        self.axilite2axi = AXI2AXILite(self.axi, self.axi_in)# self.serwb_master_core.bus)
        self.submodules += self.axilite2axi

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
        self.ram_bus = ram_bus =  AXILiteInterface(data_width=self.axi_dw, address_width=self.axi_adrw)

        self.comb += connect_axi(self.axi_out, ram_bus, omit={'addr'})
        self.comb += [
            ram_bus.ar.addr.eq(self.axi_out.ar.addr & (test_ram_address - 1)),
            ram_bus.aw.addr.eq(self.axi_out.aw.addr & (test_ram_address - 1)),
            ]
        self.sram = AXILiteSRAM(11*1024, bus=self.axi_out, init={0x89abcdef, 0xf891bcde, 0xef89abcd, 0xdef89abc, 0xcdef89ab, 0xbcdef89a, 0xabcdef89, 0x9abcdef8})
        self.submodules += self.sram


class DUTAXI2AXILiteSimple(LiteXModule):
    def __init__(self, axi_dw=32, axi_adrw=32, **kwargs):
        self.axi_dw = axi_dw
        self.axi_adrw = axi_adrw
        self.axi = AXIInterface(data_width=self.axi_dw, address_width=self.axi_adrw, id_width=8)
        self.axi_lite = AXILiteInterface(data_width=self.axi_dw, address_width=self.axi_adrw)
        self.axilite2axi = AXI2AXILite(self.axi, self.axi_lite)
        self.submodules += self.axilite2axi
        self.sram = AXILiteSRAM(11*1024, bus=self.axi_lite, init={0x89abcdef, 0xf891bcde, 0xef89abcd, 0xdef89abc, 0xcdef89ab, 0xbcdef89a, 0xabcdef89, 0x9abcdef8})
        self.submodules += self.sram
# Test ----------------------------------------------------------------------------------

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
            data_base   = 0x4000_0000
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
                acc_errors += dut.errors


                axi_adrw = 32
                axi_dw = dw
                dut = DUTAXI(axi_dw=axi_dw, axi_adrw=axi_adrw)
                dut.errors = 0
                run_simulation(dut, generator(dut), vcd_name=f'test_axipacketizer_dw{axi_dw}_adrw{axi_adrw}.vcd')
                acc_errors += dut.errors
        self.assertEqual(acc_errors, 0)

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
                    if attempts > 300:
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
                        print('read error:', i, hex((yield axi_port.r.data)), hex(data))
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
        dut = DUTOnlyAXIFull(axi_dw=axi_dw, axi_adrw=axi_adrw, test_ram_address=0x4000_0000)

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
        self._test_axifull(naccesses=2, simultaneous_writes_reads=False, axi_dw=64)

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

