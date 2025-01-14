# © 2021 Intel Corporation
# SPDX-License-Identifier: MPL-2.0

from simics import Sim_PE_No_Exception
import stest

b = obj.bank.b

trans = transaction_t(read=True, size=4)
stest.expect_equal((b.iface.transaction.issue(trans, 3),
                    b.read_offset, trans.value_le, b.read_mask, b.read_aux),
                   (Sim_PE_No_Exception, 3, 4711, 0xffffffff, 1234))
with stest.expect_log_mgr(obj, 'spec-viol'):
    stest.expect_equal(b.iface.transaction.issue(trans, 13),
                       Sim_PE_IO_Not_Taken)

trans = transaction_t(read=True, inquiry=True, size=4)
stest.expect_equal((b.iface.transaction.issue(trans, 3),
                    b.get_offset, trans.value_le, b.get_size),
                   (Sim_PE_No_Exception, 3, 4711, 4,))
stest.expect_equal(b.iface.transaction.issue(trans, 13),
                   Sim_PE_IO_Not_Taken)

trans = transaction_t(write=True, data=b'\x21\x43\x00')
stest.expect_equal((b.iface.transaction.issue(trans, 5),
                    b.write_offset, b.write_value, b.write_mask, b.write_aux),
                   (Sim_PE_No_Exception, 5, 0x4321, 0xffffff, 1234))
with stest.expect_log_mgr(obj, 'spec-viol'):
    stest.expect_equal(b.iface.transaction.issue(trans, 13),
                       Sim_PE_IO_Not_Taken)

trans = transaction_t(write=True, inquiry=True, data=b'\x11\x22\x33')
stest.expect_equal((b.iface.transaction.issue(trans, 5),
                    b.set_offset, b.set_value, b.set_size),
                   (Sim_PE_No_Exception, 5, 0x332211, 3))
stest.expect_equal(b.iface.transaction.issue(trans, 13),
                   Sim_PE_No_Exception)
