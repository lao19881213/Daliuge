#
#    ICRAR - International Centre for Radio Astronomy Research
#    (c) UWA - The University of Western Australia, 2015
#    Copyright by UWA (in the framework of the ICRAR)
#    All rights reserved
#
#    This library is free software; you can redistribute it and/or
#    modify it under the terms of the GNU Lesser General Public
#    License as published by the Free Software Foundation; either
#    version 2.1 of the License, or (at your option) any later version.
#
#    This library is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#    Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public
#    License along with this library; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston,
#    MA 02111-1307  USA
#

from dfms.data_object import FileDataObject, AppConsumer, InMemoryDataObject, InMemoryCRCResultDataObject,\
    ContainerDataObject, ContainerAppConsumer, InMemorySocketListenerDataObject,\
    NullDataObject, ImmediateAppConsumer
from dfms.events.event_broadcaster import LocalEventBroadcaster,\
    ThreadedEventBroadcaster

import os, unittest, threading
from cStringIO import StringIO
from dfms import doutils
from dfms.ddap_protocol import DOStates, ExecutionMode
from dfms.doutils import EvtConsumer
import random
import shutil

try:
    from crc32c import crc32
except:
    from binascii import crc32

ONE_MB = 1024 ** 2

def _start_ns_thread(ns_daemon):
    ns_daemon.requestLoop()

def isContainer(do):
    return isinstance(do, ContainerDataObject)

class SumupContainerChecksum(AppConsumer, InMemoryDataObject):
    """
    A dummy AppConsumer/DataObject that recursivelly sums up the checksums of
    all children of the ContainerDataObject it consumes, and then stores the
    final result in memory
    """
    def run(self, dataObject):
        if not isContainer(dataObject):
            raise Exception("This consumer consumes only Container DataObjects")
        crcSum = self.sumUpCRC(dataObject, 0)
        self.write(str(crcSum))
        self.setCompleted()

    def sumUpCRC(self, container, crcSum):
        for c in container.children:
            if isContainer(c):
                crcSum += self.sumUpCRC(container, crcSum)
            else:
                crcSum += c.checksum
        return crcSum

class TestDataObject(unittest.TestCase):

    def setUp(self):
        """
        library-specific setup
        """
        self._test_do_sz = 16 # MB
        self._test_block_sz =  2 # MB
        self._test_num_blocks = self._test_do_sz / self._test_block_sz
        self._test_block = str(bytearray(os.urandom(self._test_block_sz * ONE_MB)))

    def tearDown(self):
        shutil.rmtree("/tmp/sdp_dfms", True)

    def test_NullDataObject(self):
        """
        Check that the NullDataObject is usable for testing
        """
        a = NullDataObject('A', 'A', LocalEventBroadcaster(), expectedSize=5)
        a.write("1234")
        a.write("5")
        allContents = doutils.allDataObjectContents(a)
        self.assertEquals(None, allContents)

    def test_write_FileDataObject(self):
        """
        Test a FileDataObject and a simple AppDataObject (for checksum calculation)
        """
        self._test_write_withDataObjectType(FileDataObject)

    def test_write_InMemoryDataObject(self):
        """
        Test an InMemoryDataObject and a simple AppDataObject (for checksum calculation)
        """
        self._test_write_withDataObjectType(InMemoryDataObject)

    def _test_write_withDataObjectType(self, doType):
        """
        Test an AbstractDataObject and a simple AppDataObject (for checksum calculation)
        """
        eventbc=LocalEventBroadcaster()

        dobA = doType('oid:A', 'uid:A', eventbc, expectedSize = self._test_do_sz * ONE_MB)
        dobB = InMemoryCRCResultDataObject('oid:B', 'uid:B', eventbc)
        dobA.addConsumer(dobB)

        test_crc = 0
        for _ in range(self._test_num_blocks):
            dobA.write(self._test_block)
            test_crc = crc32(self._test_block, test_crc)

        # Read the checksum from dobB
        dobBChecksum = int(doutils.allDataObjectContents(dobB))

        self.assertNotEquals(dobA.checksum, 0)
        self.assertEquals(dobA.checksum, test_crc)
        self.assertEquals(dobBChecksum, test_crc)

    def test_simple_chain(self):
        '''
        Simple test that creates a pipeline-like chain of commands.
        In this case we simulate a pipeline that does this, holding
        each intermediate result in memory:

        cat someFile | grep 'a' | sort | rev
        '''

        class GrepResult(AppConsumer):
            def appInitialize(self, **kwargs):
                self._substring = kwargs['substring']

            def run(self, do):
                allLines = StringIO(doutils.allDataObjectContents(do)).readlines()
                for line in allLines:
                    if self._substring in line:
                        self.write(line)
                self.setCompleted()

        class SortResult(AppConsumer):
            def run(self, do):
                sortedLines = StringIO(doutils.allDataObjectContents(do)).readlines()
                sortedLines.sort()
                for line in sortedLines:
                    self.write(line)
                self.setCompleted()

        class RevResult(AppConsumer):
            def run(self, do):
                allLines = StringIO(doutils.allDataObjectContents(do)).readlines()
                for line in allLines:
                    buf = ''
                    for c in line:
                        if c == ' ' or c == '\n':
                            self.write(buf[::-1])
                            self.write(c)
                            buf = ''
                        else:
                            buf += c
                self.setCompleted()

        class InMemoryGrepResult(GrepResult, InMemoryDataObject): pass
        class InMemorySortResult(SortResult, InMemoryDataObject): pass
        class InMemoryRevResult(RevResult, InMemoryDataObject): pass

        leb = LocalEventBroadcaster()
        a = InMemoryDataObject('oid:A', 'uid:A', leb)
        b = InMemoryGrepResult('oid:B', 'uid:B', leb, substring="a")
        c = InMemorySortResult('oid:C', 'uid:C', leb)
        d = InMemoryRevResult('oid:D', 'uid:D', leb)

        a.addConsumer(b)
        b.addConsumer(c)
        c.addConsumer(d)

        # Initial write
        contents = "first line\nwe have an a here\nand another one\nnoone knows me"
        bResExpected = "we have an a here\nand another one\n"
        cResExpected = "and another one\nwe have an a here\n"
        dResExpected = "dna rehtona eno\new evah na a ereh\n"
        a.write(contents)
        a.setCompleted()

        # Get intermediate and final results and compare
        actualRes   = []
        for i in [b, c, d]:
            desc = i.open()
            actualRes.append(i.read(desc))
            i.close(desc)
        map(lambda x, y: self.assertEquals(x, y), [bResExpected, cResExpected, dResExpected], actualRes)

    def test_join_simple(self):
        self._test_join(False)

    def test_join_threaded(self):
        self._test_join(True)

    def _test_join(self, threaded):
        """
        Using the container data object to implement a join/barrier dataflow.

        A1, A2 and A3 are FileDataObjects
        B1, B2 and B3 are CRCResultDataObjects
        C is a ContainerDataObject
        D is a SumupContainerChecksum

        --> A1 --> B1 --|
        --> A2 --> B2 --|--> C --> D
        --> A3 --> B3 --|

        Upon writing all A* DOs, the execution of B* DOs should be triggered,
        after which "C" will transition to COMPLETE. Finally, "D" will also be
        triggered, and will hold the sum of B1, B2 and B3's contents
        """

        eventbc = ThreadedEventBroadcaster() if threaded else LocalEventBroadcaster()

        filelen = self._test_do_sz * ONE_MB
        #create file data objects
        doA1 = FileDataObject('oid:A1', 'uid:A1', eventbc, expectedSize=filelen)
        doA2 = FileDataObject('oid:A2', 'uid:A2', eventbc, expectedSize=filelen)
        doA3 = FileDataObject('oid:A3', 'uid:A3', eventbc, expectedSize=filelen)

        # CRC Result DOs, storing the result in memory
        doB1 = InMemoryCRCResultDataObject('oid:B1', 'uid:B1', eventbc)
        doB2 = InMemoryCRCResultDataObject('oid:B2', 'uid:B2', eventbc)
        doB3 = InMemoryCRCResultDataObject('oid:B3', 'uid:B3', eventbc)

        # The Container DO that groups together the CRC Result DOs
        doC = ContainerDataObject('oid:C', 'uid:C', eventbc)

        # The final DO that sums up the CRCs from the container DO
        doD = SumupContainerChecksum('oid:D', 'uid:D', eventbc)

        # Wire together
        doAList = [doA1,doA2,doA3]
        doBList = [doB1,doB2,doB3]
        for doA,doB in map(lambda a,b: (a,b), doAList, doBList):
            doA.addConsumer(doB)
        for doB in doBList:
            doC.addChild(doB)
        doC.addConsumer(doD)

        # Wait until D is completed
        evt = threading.Event()
        doD.addConsumer(EvtConsumer(evt))

        # Write data into the initial "A" DOs, which should trigger
        # the whole chain explained above
        for dobA in doAList: # this should be parallel for
            for _ in range(self._test_num_blocks):
                dobA.write(self._test_block)
        evt.wait()

        # All DOs are completed now that the chain executed correctly
        for do in doAList + doBList:
            self.assertTrue(do.status, DOStates.COMPLETED)

        # The results we want to compare
        sum_crc = doB1.checksum + doB2.checksum + doB3.checksum
        dobDData = int(doutils.allDataObjectContents(doD))

        self.assertNotEquals(sum_crc, 0)
        self.assertEquals(sum_crc, dobDData)

    def test_lmc(self):
        """
        A more complex test that simulates the LMC (or DataFlowManager)
        submitting a physical graph via the DataManager, and in turn via two
        different DOMs. The graph that gets submitted looks like this:

           -----------------Data-Island------------------
          |                     |                       |
          | A --------> B ------|------> C --------> D  |
          |   Data Object Mgr   |    Data Object Mgr    |
          |       001           |        002            |
          -----------------------------------------------

        Here only A is a FileDataObject; B, C and D are
        InMemoryCRCResultDataObject, meaning that D holds
        A's checksum's checksum's checksum.

        The most interesting part of this exercise though is that it
        crosses boundaries of DOMs, and show that DOs are correctly
        talking to each other remotely in the current prototype with
        Pyro and Pyro4 (or not)
        """

        import datetime
        import Pyro4
        from dfms.data_manager import DataManager
        from dfms import data_object_mgr, dataflow_manager

        ns_host = 'localhost'
        my_host = 'localhost'
        my_port = 7778

        # 1.1. launch Pyro4 name service, DOMs register on it
        _, ns4Daemon, _ = Pyro4.naming.startNS(host=ns_host)
        ns4Thread = threading.Thread(None, lambda x: x.requestLoop(), 'NS4Thrd', [ns4Daemon])
        ns4Thread.setDaemon(1)
        ns4Thread.start()

        # Now comes the real work
        try:
            # 2. launch data_object_manager
            data_object_mgr.launchServer('001', as_daemon=True, nsHost=ns_host, host=my_host, port=my_port)
            data_object_mgr.launchServer('002', as_daemon=True, nsHost=ns_host, host=my_host, port=my_port+1)

            # 3. ask dataflow_manager to build the physical dataflow
            obsId = datetime.datetime.now().strftime('%Y-%m-%dT%H-%M-%S.%f') # a dummy observation id
            (pdgRoot, doms) = dataflow_manager.buildSimpleIngestPDG(obsId, nsHost=ns_host)

            a = pdgRoot
            b = a.consumers[0]
            c = b.consumers[0]
            d = c.consumers[0]
            for do in [a,b,c,d]:
                self.assertTrue(do.status, DOStates.INITIALIZED)

            # 4. start a single data manager
            print "**** step 4"
            dmgr = DataManager()
            dmgr.start() # start the daemon

            print "**** step 5"
            # 5. submit the graph to data manager
            res_avail = dmgr.submitPDG(pdgRoot, doms)
            if (not res_avail):
                raise Exception("Resource is not available in the data manager!")

            print "**** step 6"
            # 6. start the pipeline (simulate CSP)
            # Since the events are asynchronously we wait
            # on an event set when D is COMPLETED
            evt = threading.Event()
            consumer = EvtConsumer(evt)
            daemon = Pyro4.Daemon()
            consumerUri = daemon.register(consumer)
            t = threading.Thread(None, lambda: daemon.requestLoop(), "tmp", [])
            t.start()
            d.addConsumer(Pyro4.Proxy(consumerUri))

            pdgRoot.write(' ')
            pdgRoot.setCompleted()
            self.assertTrue(evt.wait(5)) # Should take only a fraction of a second anyway
            daemon.shutdown()
            t.join()

            for do in [a,b,c,d]:
                self.assertEquals(do.status, DOStates.COMPLETED)

            # Check that B holds A's checksum and so forth
            for prod, cons in [(a,b), (b,c), (c,d)]:
                consContents = int(doutils.allDataObjectContents(cons))
                self.assertEquals(prod.checksum, consContents,
                                  "%s/%s's checksum did not match %s/%s's content: %d/%d" %
                                  (a.oid, a.uid, b.oid, b.uid, prod.checksum, consContents))

            print "**** step 7"
            # 7. tear down data objects of this observation on each data object manager
            for dom in doms:
                ret = dom.shutdownDOBDaemon(obsId)
                print '%s was shutdown, ret code = %d' % (dom.getURI(), ret)

            # 8. shutdown the data manager daemon
            dmgr.shutdown()

        except Exception:
            print("Pyro traceback:")
            print("".join(Pyro4.util.getPyroTraceback()))
            raise
        finally:
            # 9. shutdown name service
            try:
                ns4Daemon.shutdown()
                ns4Thread.join()
            except:
                pass

    def test_container_app_do(self):
        """
        A small method that tests that the ContainerAppConsumer concept works

        The graph constructed by this example looks as follow:

                        |--> D
        A --> B --> C --|
                        |--> E

        Here C is a ContainerAppConsumer, meaning that it consumes the data
        from B and fills the D and E DataObjects, which are its children.
        """

        class NumberWriterApp(InMemoryDataObject, AppConsumer):
            def run(self, dataObject):
                howMany = int(doutils.allDataObjectContents(dataObject))
                for i in xrange(howMany):
                    self.write(str(i) + " ")
                self.setCompleted()

        class OddAndEvenContainerApp(ContainerAppConsumer):
            def run(self, dataObject):
                numbers = doutils.allDataObjectContents(dataObject).strip().split()
                for n in numbers:
                    self._children[int(n) % 2].write(n + " ")
                self._children[0].setCompleted()
                self._children[1].setCompleted()

        # Create DOs
        eb = LocalEventBroadcaster()
        a =     InMemoryDataObject('oid:A', 'uid:A', eb)
        b =        NumberWriterApp('oid:B', 'uid:B', eb)
        c = OddAndEvenContainerApp('oid:C', 'uid:C', eb)
        d =     InMemoryDataObject('oid:D', 'uid:D', eb)
        e =     InMemoryDataObject('oid:E', 'uid:E', eb)

        # Wire them together
        a.addConsumer(b)
        b.addConsumer(c)
        c.addChild(d)
        c.addChild(e)

        # Start the execution
        a.write('20')
        a.setCompleted()


        # Check the final results are correct
        for do in [a,b,c,d,e]:
            self.assertEquals(do.status, DOStates.COMPLETED)
        self.assertEquals("0 2 4 6 8 10 12 14 16 18", doutils.allDataObjectContents(d).strip())
        self.assertEquals("1 3 5 7 9 11 13 15 17 19", doutils.allDataObjectContents(e).strip())

    def test_socket_listener(self):
        '''
        A simple test to check that SocketListeners are indeed working as expected;
        that is, they write the data they receive into themselves, and set themselves
        as completed when the connection is closed from the client side

        The data flow diagram looks like this:

        clientSocket --> A --> B
        '''

        host = 'localhost'
        port = 9933
        ebc = LocalEventBroadcaster()
        data = 'shine on you crazy diamond'

        a = InMemorySocketListenerDataObject('oid:A', 'uid:A', ebc, host=host, port=port)
        b = InMemoryCRCResultDataObject('oid:B', 'uid:B', ebc)
        a.addConsumer(b)

        # Since b becomes COMPLETED on a different thread (where A's socket is
        # listening for data) we need to wait on an Event
        evt = threading.Event()
        b.addConsumer(EvtConsumer(evt))

        # Create the socket, write, and close the connection, allowing
        # A to move to COMPLETED
        import socket
        socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        socket.connect((host, port))
        socket.send(data)
        socket.close()

        evt.wait(3) # That's plenty of time

        for do in [a,b]:
            self.assertEquals(DOStates.COMPLETED, do.status)

        # Our expectations are fulfilled!
        aContents = doutils.allDataObjectContents(a)
        bContents = int(doutils.allDataObjectContents(b))
        self.assertEquals(data, aContents)
        self.assertEquals(crc32(data, 0), bContents)

    def test_dataObjectWroteFromOutside(self):
        """
        A different scenario to those tested above, in which the data
        represented by the DataObject isn't actually written *through* the
        DataObject. Still, the DataObject needs to be moved to COMPLETED once
        the data is written, and reading from it should still yield a correct
        result
        """

        # Write, but not through the DO
        a = FileDataObject('A', 'A', LocalEventBroadcaster())
        filename = a.getFileName()
        msg = 'a message'
        with open(filename, 'w') as f:
            f.write(msg)
        a.setCompleted()

        # Read from the DO
        self.assertEquals(msg, doutils.allDataObjectContents(a))
        self.assertIsNone(a.checksum)
        self.assertIsNone(a.size)

        # We can manually set the size because the DO wasn't able to calculate
        # it itself; if we couldn't an exception would be thrown
        a.size = len(msg)

    def test_stateMachine(self):
        """
        A simple test to check that some transitions are invalid
        """

        # Nice and easy
        do = InMemoryDataObject('a', 'a', LocalEventBroadcaster())
        self.assertEquals(do.status, DOStates.INITIALIZED)
        do.write('a')
        self.assertEquals(do.status, DOStates.WRITING)
        do.setCompleted()
        self.assertEquals(do.status, DOStates.COMPLETED)

        # Try to overwrite the DO's checksum and size
        self.assertRaises(Exception, lambda: setattr(do, 'checksum', 0))
        self.assertRaises(Exception, lambda: setattr(do, 'size', 0))

        # Try to write on a DO that is already COMPLETED
        self.assertRaises(Exception, do.write, '')

        # Failure to initialize (ports < 1024 cannot be opened by normal users)
        self.assertRaises(Exception, InMemorySocketListenerDataObject, 'a', 'a', LocalEventBroadcaster(), host='localhost', port=1)

        # Invalid reading on a DO that isn't COMPLETED yet
        do = InMemoryDataObject('a', 'a', LocalEventBroadcaster())
        self.assertRaises(Exception, do.open)
        self.assertRaises(Exception, do.read, 1)
        self.assertRaises(Exception, do.close, 1)

        # Invalid file descriptors used to read/close
        do.setCompleted()
        fd = do.open()
        otherFd = random.SystemRandom().randint(0, 1000)
        self.assertNotEquals(fd, otherFd)
        self.assertRaises(Exception, do.read, otherFd)
        self.assertRaises(Exception, do.close, otherFd)
        # but using the correct one should be OK
        do.read(fd)
        self.assertTrue(do.isBeingRead())
        do.close(fd)

        # Expire it, then try to set it as COMPLETED again
        do.status = DOStates.EXPIRED
        self.assertRaises(Exception, do.setCompleted)

    def test_externalGraphExecutionDriver(self):
        self._test_graphExecutionDriver(ExecutionMode.EXTERNAL)

    def test_DOGraphExecutionDriver(self):
        self._test_graphExecutionDriver(ExecutionMode.DO)

    def _test_graphExecutionDriver(self, mode):
        """
        A small test to check that DOs executions can be driven externally if
        required, and not always internally by themselves
        """
        eb = LocalEventBroadcaster()
        a = InMemoryDataObject('a', 'a', eb, executionMode=mode, expectedSize=1)
        b = InMemoryCRCResultDataObject('b', 'b', eb)
        a.addConsumer(b)
        a.write('1')

        if mode == ExecutionMode.EXTERNAL:
            # b hasn't been triggered
            self.assertEquals(b.status, DOStates.INITIALIZED)
            # Now let b consume a
            b.consume(a)
            self.assertEquals(b.status, DOStates.COMPLETED)
        elif mode == ExecutionMode.DO:
            # b is already done
            self.assertEquals(b.status, DOStates.COMPLETED)

    def test_immediateConsumer(self):
        """
        A test for immediate consumers, which consume a DO's data as it gets
        written into the DO. We use the following graph:

        A --|--> B
            |--> C

        Here B is an immediate consumer of A, while C is a normal one.
        """

        class LastCharWriterApp(ImmediateAppConsumer):
            def appInitialize(self, **kwargs):
                self._lastChar = None
            def consume(self, data):
                self._lastChar = data[-1]
                self.write(self._lastChar)
            def consumptionCompleted(self):
                self.setCompleted()
        class InMemoryLastCharWriterApp(LastCharWriterApp, InMemoryDataObject):
            pass

        eb = LocalEventBroadcaster()
        a = InMemoryDataObject('a', 'a', eb)
        b = InMemoryLastCharWriterApp('b', 'b', eb)
        c = InMemoryCRCResultDataObject('c', 'c', eb) # this is a normal AppConsumer
        a.addImmediateConsumer(b)
        a.addConsumer(c)

        # Consumer cannot be normal and immediate at the same time
        self.assertRaises(Exception, lambda: a.addConsumer(b))
        self.assertRaises(Exception, lambda: a.addImmediateConsumer(c))

        # Write a little, then check the consumers
        def checkDOStates(aStatus, bStatus, cStatus, lastChar):
            self.assertEquals(aStatus, a.status)
            self.assertEquals(bStatus, b.status)
            self.assertEquals(cStatus, c.status)
            self.assertEquals(lastChar, b._lastChar)

        checkDOStates(DOStates.INITIALIZED , DOStates.INITIALIZED, DOStates.INITIALIZED, None)
        a.write('abcde')
        checkDOStates(DOStates.WRITING, DOStates.WRITING, DOStates.INITIALIZED, 'e')
        a.write('fghij')
        checkDOStates(DOStates.WRITING, DOStates.WRITING, DOStates.INITIALIZED, 'j')
        a.write('k')
        a.setCompleted()
        checkDOStates(DOStates.COMPLETED, DOStates.COMPLETED, DOStates.COMPLETED, 'k')

        self.assertEquals('ejk', doutils.allDataObjectContents(b))

if __name__ == '__main__':
    unittest.main()