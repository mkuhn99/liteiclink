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
CHANNELS = ['aw', 'w', 'ar', 'r', 'b']
# Fake Init/Serdes/PHY -----------------------------------------------------------------------------

class FakeInit(LiteXModule):
    def __init__(self):
        self.ready = Signal(reset=1)


class FakeSerdes(LiteXModule):
    def __init__(self, dw):
        self.tx_dp = TXDatapath(8, packet_dw=dw)
        self.rx_dp = RXDatapath(8, packet_dw=dw)
        self.comb += [self.rx_dp.shift_inc.eq(0)]


class FakePHY(LiteXModule):
    def __init__(self, dw=32):
        self.sink   = sink   = stream.Endpoint([("data", dw)])
        self.source = source = stream.Endpoint([("data", dw)])

        # # #

        self.init   = FakeInit()
        self.serdes = FakeSerdes(dw)

        # TX dataflow
        self.comb += [
            If(self.init.ready,
                sink.ready.eq(1),
                If(sink.valid,
                    self.sink.connect(self.serdes.tx_dp.sink),
                )
            )
        ]

        # RX dataflow
        self.comb += [
            If(self.init.ready,
                self.serdes.rx_dp.source.connect(source),
            )
        ]

# DUT Core -----------------------------------------------------------------------------------------

class DUTCore(LiteXModule):
    def __init__(self, **kwargs):
        # AXI slave
        self.slave_aw_phy = FakePHY()
        self.slave_w_phy = FakePHY()
        self.slave_r_phy = FakePHY()
        self.slave_ar_phy = FakePHY()
        self.slave_b_phy = FakePHY()
        self.phy_slaves = phy_slaves = {'aw':self.slave_aw_phy, 'w':self.slave_w_phy, 'r':self.slave_r_phy, 'ar':self.slave_ar_phy, 'b':self.slave_b_phy}
        serwb_slave = SERWBCoreAXILite(phy_slaves, int(1e6), mode="slave", buffer_depth=6)
        self.submodules += serwb_slave


        # AXI master
        self.master_aw_phy = FakePHY()
        self.master_w_phy = FakePHY()
        self.master_r_phy = FakePHY()
        self.master_ar_phy = FakePHY()
        self.master_b_phy = FakePHY()

        self.phy_masters = phy_masters = {'w':self.master_w_phy, 'aw':self.master_aw_phy, 'r':self.master_r_phy, 'ar':self.master_ar_phy, 'b':self.master_b_phy}
        serwb_master = SERWBCoreAXILite(phy_masters, int(1e6), mode="master", buffer_depth=6)
        self.submodules += serwb_master
        for k in CHANNELS:
            self.submodules += phy_slaves[k], phy_masters[k]
            # Connect phy
            self.comb += [
                phy_slaves[k].serdes.tx_dp.source.connect(phy_masters[k].serdes.rx_dp.sink),# omit={'valid', 'ready'}),
                phy_masters[k].serdes.tx_dp.source.connect(phy_slaves[k].serdes.rx_dp.sink),# omit={'valid', 'ready'}),
            ]

        # Add AXI sram to AXI master
        sram = AXILiteSRAM(1024, bus=serwb_master.bus, init={0x5aa11aa5, 0x5aa22aa5, 0x5aa33aa5})
        self.submodules += sram

        # Expose AXI slave
        self.axi_slave = serwb_slave.bus
        self.axi_master = serwb_master.bus


# Test SERWB Core ----------------------------------------------------------------------------------

class TestSERWBCore(unittest.TestCase):
    def test_serwb(self):
        def generator(dut):
            # Prepare test
            prng        = random.Random(42)
            data_base   = 0x4
            data_length = 5
            datas_w     = [prng.randrange(2**32) for i in range(data_length)]
            datas_r     = []
            # Init:

            for k in CHANNELS:
                yield dut.phy_masters[k].serdes.rx_dp.sink.valid.eq(1)
                yield dut.phy_slaves[k].serdes.rx_dp.sink.valid.eq(1)
                yield dut.phy_masters[k].serdes.rx_dp.sink.ready.eq(1)
                yield dut.phy_slaves[k].serdes.rx_dp.sink.ready.eq(1)
                yield dut.phy_masters[k].serdes.tx_dp.source.ready.eq(1)
                yield dut.phy_slaves[k].serdes.tx_dp.source.ready.eq(1)
            # Write
            yield
            yield
            yield
            yield
            yield
            yield
            for i in range(data_length):
                yield from dut.axi_slave.write((data_base + i*4), datas_w[i])

            # Read
            for i in range(data_length):
                print(i)
                datas_r.append((yield from dut.axi_slave.read((data_base + i*4)))[0])

            # Check
            print(datas_w)
            print(datas_r)
            for i in range(data_length):
                print(hex(datas_r[i]))
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

if __name__ == '__main__':
    tst = TestSERWBCore()
    tst.test_serwb()