import unittest
import random

from migen import *

from litex.gen import *

from litex.gen.sim import *

from litex.soc.interconnect import stream
from litex.soc.interconnect.axi import AXILiteSRAM, AXILiteInterface
# DUT Core Simple ------------------------------------------------------------------------------------

class DUTCoreSimple(LiteXModule):
    def __init__(self, **kwargs):
        self.s0 = stream.Endpoint([('data', 3)])
        self.s1 = stream.Endpoint([('data', 3)])
        self.s2 = stream.Endpoint([('data', 3)])

        self.cast = stream.Endpoint([('s0_data', 3), ('s0_valid', 1), ('s1_data', 3), ('s1_valid', 1), ('s2_data', 3), ('s2_valid', 1)])

        self.sync += [
            self.cast.valid.eq(self.s0.valid | self.s1.valid | self.s2.valid),
            self.cast.s0_data.eq(self.s0.data),
            self.cast.s0_valid.eq(self.s0.valid),
            self.cast.s1_data.eq(self.s1.data),
            self.cast.s1_valid.eq(self.s1.valid),
            self.cast.s2_data.eq(self.s2.data),
            self.cast.s2_valid.eq(self.s2.valid),
        ]

        self.s0_ = stream.Endpoint([('data', 3)])
        self.s1_ = stream.Endpoint([('data', 3)])
        self.s2_ = stream.Endpoint([('data', 3)])

        self.sync += [
            self.s0_.data.eq(self.cast.s0_data),
            self.s0_.valid.eq(self.cast.s0_valid & self.cast.valid),
            self.s1_.data.eq(self.cast.s1_data),
            self.s1_.valid.eq(self.cast.s1_valid & self.cast.valid),
            self.s2_.data.eq(self.cast.s2_data),
            self.s2_.valid.eq(self.cast.s2_valid & self.cast.valid),
        ]


# DUT Core ACI ------------------------------------------------------------------------------------

# Master -> Uniform 32bit Streams -> Slave -> SRAM                  
#                  Master   <------------>   Slave
# aw: 
#                            aw_addr 32 ->
# w:
#                             w_data 32 ->
# ctrl: 
#                             ar_prot 3 ->
#                             aw_prot 3 ->
#                              w_strb 4 ->
#                           <- 2 b_resp
#                           <- 2 r_resp
# r:  
#                            ar_addr 32 ->
#                           <- 32 r_data
class DUTCoreAXI(LiteXModule):
    def __init__(self, **kwargs):
        self.axi_master = AXILiteInterface()
        self.axi_slave = AXILiteInterface()

        self.aw_addr_endpoint = stream.Endpoint(stream.EndpointDescription([('data', 32)]))
        self.ar_addr_endpoint = stream.Endpoint(stream.EndpointDescription([('data', 32)]))
        self.w_data_endpoint = stream.Endpoint(stream.EndpointDescription([('data', 32)]))
        self.r_data_endpoint = stream.Endpoint(stream.EndpointDescription([('data', 32)]))
        self.ctrl_endpoint0 = stream.Endpoint(stream.EndpointDescription([('ar_prot', 3), ('ar_valid', 1), ('aw_prot', 3), ('aw_valid', 1), ('w_strb', 4), ('w_valid', 1), ('pad', 19)]))
        self.ctrl_endpoint1 = stream.Endpoint(stream.EndpointDescription([('b_resp', 3), ('b_valid', 1), ('r_resp', 3), ('r_valid', 1), ('pad', 24)]))

        self.sync += [
            self.aw_addr_endpoint.data.eq(self.axi_master.aw.addr),
            self.aw_addr_endpoint.valid.eq(self.axi_master.aw.valid),

            self.ar_addr_endpoint.data.eq(self.axi_master.ar.addr),
            self.ar_addr_endpoint.valid.eq(self.axi_master.ar.valid),

            self.w_data_endpoint.data.eq(self.axi_master.w.data),
            self.w_data_endpoint.valid.eq(self.axi_master.w.valid),


            self.r_data_endpoint.data.eq(self.axi_slave.r.data),
            self.r_data_endpoint.valid.eq(self.axi_slave.r.valid),

            self.ctrl_endpoint0.valid.eq(self.axi_master.ar.valid | self.axi_master.aw.valid | self.axi_master.w.valid),
            self.ctrl_endpoint0.ar_prot.eq(self.axi_master.ar.prot),
            self.ctrl_endpoint0.aw_prot.eq(self.axi_master.aw.prot),
            self.ctrl_endpoint0.w_strb.eq(self.axi_master.w.strb),
            self.ctrl_endpoint0.ar_valid.eq(self.axi_master.ar.valid),
            self.ctrl_endpoint0.aw_valid.eq(self.axi_master.aw.valid),
            self.ctrl_endpoint0.w_valid.eq(self.axi_master.w.valid),

            self.ctrl_endpoint1.valid.eq(self.axi_slave.b.valid | self.axi_slave.r.valid),
            self.ctrl_endpoint1.b_resp.eq(self.axi_slave.b.resp),
            self.ctrl_endpoint1.b_valid.eq(self.axi_slave.b.valid),
            self.ctrl_endpoint1.r_resp.eq(self.axi_slave.r.resp),
            self.ctrl_endpoint1.r_valid.eq(self.axi_slave.r.valid),
        ]

        self.sync += [
            self.axi_slave.aw.addr.eq(self.aw_addr_endpoint.data),
            self.axi_slave.aw.prot.eq(self.ctrl_endpoint0.aw_prot),
            self.axi_slave.aw.valid.eq(self.ctrl_endpoint0.aw_valid & self.aw_addr_endpoint.valid & self.ctrl_endpoint0.valid),


            self.axi_slave.w.data.eq(self.w_data_endpoint.data),
            self.axi_slave.w.strb.eq(self.ctrl_endpoint0.w_strb),
            self.axi_slave.w.valid.eq(self.ctrl_endpoint0.w_valid & self.w_data_endpoint.valid & self.ctrl_endpoint0.valid),

            self.axi_master.r.data.eq(self.r_data_endpoint.data),
            self.axi_master.r.resp.eq(self.ctrl_endpoint1.r_resp),
            self.axi_master.r.valid.eq(self.r_data_endpoint.valid & self.ctrl_endpoint1.valid & self.ctrl_endpoint1.r_valid),

            self.axi_slave.ar.addr.eq(self.ar_addr_endpoint.data),
            self.axi_slave.ar.prot.eq(self.ctrl_endpoint0.ar_prot),
            self.axi_slave.ar.valid.eq(self.ctrl_endpoint0.ar_valid & self.ar_addr_endpoint.valid & self.ctrl_endpoint0.valid),

            self.axi_master.b.resp.eq(self.ctrl_endpoint1.b_resp),
            self.axi_master.b.valid.eq(self.ctrl_endpoint1.b_valid & self.ctrl_endpoint1.valid),
        ]

        self.comb += [
            self.axi_master.ar.ready.eq(1),
            self.axi_master.aw.ready.eq(1),
            self.axi_master.w.ready.eq(1),
            self.axi_master.r.ready.eq(1),
            self.axi_master.b.ready.eq(1),

            self.axi_slave.ar.ready.eq(1),
            self.axi_slave.aw.ready.eq(1),
            self.axi_slave.w.ready.eq(1),
            self.axi_slave.r.ready.eq(1),
            self.axi_slave.b.ready.eq(1),
        ]
        sram = AXILiteSRAM(256, bus=self.axi_slave, init={0x5aa55aa5, 0x5aa55aa5, 0x5aa55aa5})
        self.submodules += sram

# Test SERWB Core ----------------------------------------------------------------------------------

class TestSERWBCore(unittest.TestCase):
    def test_simple(self):
        def generator(dut):
            yield dut.s0.data.eq(0b101)
            yield dut.s0.valid.eq(1)
            yield dut.s1.data.eq(0b111)
            yield
            yield dut.s0.valid.eq(0)
            yield dut.s2.data.eq(0b001)
            yield dut.s2.valid.eq(1)
            yield
            yield dut.s2.valid.eq(0)
            while not (yield dut.s2_.valid):
                if (yield dut.s0_.valid):
                    if (yield dut.s0_.data) != 0b101:
                        dut.errors += 1
                if (yield dut.s1_.valid):
                    if (yield dut.s1_.data) == 0b111:
                        dut.errors += 1
                yield
            if (yield dut.s2_.valid):
                if (yield dut.s2_.data) != 0b001:
                    dut.errors += 1
            for i in range(10):
                yield
            
            yield dut.s0.data.eq(0b100)
            yield dut.s0.valid.eq(1)
            yield
            yield dut.s1.data.eq(0b010)
            yield dut.s1.valid.eq(1)
            yield dut.s0.valid.eq(0)
            yield dut.s2.data.eq(0b101)
            yield dut.s2.valid.eq(1)
            yield
            yield dut.s1.valid.eq(0)
            yield dut.s2.valid.eq(0)
            while not (yield dut.s2_.valid):
                if (yield dut.s0_.valid):
                    if (yield dut.s0_.data) != 0b100:
                        dut.errors += 1
                if (yield dut.s1_.valid):
                    if (yield dut.s1_.data) == 0b111:
                        dut.errors += 1
                yield
            if (yield dut.s2_.valid):
                if (yield dut.s2_.data) != 0b101:
                    dut.errors += 1
            else:
                dut.errors += 1
            if (yield dut.s1_.valid):
                if (yield dut.s1_.data) != 0b010:
                    dut.errors += 1
            else:
                dut.errors += 1
        dut = DUTCoreSimple()
        dut.errors = 0
        run_simulation(dut, generator(dut), vcd_name='test_stream_cast_simple.vcd')
        self.assertEqual(dut.errors, 0)

    def test_axi(self):
        def generator(dut):
            for data in [0x89abcdef, 0x0, 0x1, 0xffffffff, 0x2, 0x7892324]:
                yield from dut.axi_master.write(0, data)
                r = (yield from dut.axi_master.read(0))[0]
                if r != data:
                    dut.errors += 1
        dut = DUTCoreAXI()
        dut.errors = 0
        run_simulation(dut, generator(dut), vcd_name='test_stream_cast_axi.vcd')
        self.assertEqual(dut.errors, 0)