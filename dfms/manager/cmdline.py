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
"""
Module containing command-line entry points to launch Data Manager instances
like DMs and DIMs.
"""

import logging
import optparse
import os
import signal
import sys
import threading

import daemon
from lockfile.pidlockfile import PIDLockFile

from dfms.manager.composite_manager import DataIslandManager, MasterManager
from dfms.manager.constants import NODE_DEFAULT_REST_PORT, \
    ISLAND_DEFAULT_REST_PORT, MASTER_DEFAULT_REST_PORT
from dfms.manager.node_manager import NodeManager
from dfms.manager.rest import NMRestServer, CompositeManagerRestServer, \
    MasterManagerRestServer
from dfms.utils import getDfmsPidDir, getDfmsLogsDir, createDirIfMissing


_terminating = False
def launchServer(opts):

    # we might be called via __main__, but we want a nice logger name
    logger = logging.getLogger('dfms.manager.cmdline')
    dmName = opts.dmType.__name__

    logger.info('Creating %s' % (dmName))
    dm = opts.dmType(*opts.dmArgs, **opts.dmKwargs)

    server = opts.restType(dm)

    # Signal handling
    def handle_signal(signNo, stack_frame):
        global _terminating
        if _terminating:
            return
        _terminating = True
        logger.info("Exiting from %s" % (dmName))

        # Stop pyro first, cleanup the manager later
        if hasattr(dm, 'shutdown'):
            dm.shutdown()

        server.stop()
        logger.info("Thanks for using our %s, come back again :-)" % (dmName))

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    server_t = threading.Thread(target=server.start, args=(opts.host, opts.port))
    server_t.start()
    # Now simply wait...
    signal.pause()


def addCommonOptions(parser, defaultPort):
    parser.add_option("-H", "--host", action="store", type="string",
                      dest="host", help = "The host to bind this instance on", default='localhost')
    parser.add_option("-P", "--port", action="store", type="int",
                      dest="port", help = "The port to bind this instance on", default=defaultPort)
    parser.add_option("-d", "--daemon", action="store_true",
                      dest="daemon", help="Run as daemon", default=False)
    parser.add_option("-s", "--stop", action="store_true",
                      dest="stop", help="Stop an instance running as daemon", default=False)
    parser.add_option("-v", "--verbose", action="count",
                      dest="verbose", help="Become more verbose. The more flags, the more verbose")
    parser.add_option("-q", "--quiet", action="count",
                      dest="quiet", help="Be less verbose. The more flags, the quieter")
    parser.add_option("--log-dir", action="store", type="string",
                      dest="logdir", help="The directory where the logging files will be stored", default=getDfmsLogsDir())

def commonOptionsCheck(options, parser):
    # -d and -s are exclusive
    if options.daemon and options.stop:
        parser.error('-d and -s cannot be specified together')
    # -v and -q are exclusive
    if options.verbose and options.quiet:
        parser.error('-v and -q cannot be specified together')

def start(options, parser):

    # Perform common option checks
    commonOptionsCheck(options, parser)

    # Setup the loggers
    setupLogging(options)

    # Start daemon?
    if options.daemon:

        # Make sure the PID file will be created without problems
        pidDir  = getDfmsPidDir()
        createDirIfMissing(pidDir)
        pidfile = os.path.join(pidDir,  "dfms%s.pid"    % (options.dmAcronym))

        with daemon.DaemonContext(pidfile=PIDLockFile(pidfile, 1)):
            launchServer(options)

    # Stop daemon?
    elif options.stop:
        pidDir = getDfmsPidDir()
        pidfile = os.path.join(pidDir,  "dfms%s.pid"    % (options.dmAcronym))
        pid = PIDLockFile(pidfile).read_pid()
        if pid is None:
            sys.stderr.write('Cannot read PID file, is there an instance running?\n')
        else:
            os.kill(pid, signal.SIGTERM)

    # Start directly
    else:
        launchServer(options)

def setupLogging(opts):
    if logging.root.handlers:
        # Mmmm, somebody already did some logging, it shouldn't have been us
        # Let's reset the root handlers
        for h in logging.root.handlers[:]:
            logging.root.removeHandler(h)
        pass

    levels = [
        logging.NOTSET,
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL
    ]

    # Default is WARNING
    lidx = 3
    if opts.verbose:
        lidx -= min((opts.verbose, 3))
    elif opts.quiet:
        lidx += min((opts.quiet, 2))
    level = levels[lidx]

    # Let's configure logging now
    # Daemons don't output stuff to the stdout
    fmt = logging.Formatter("%(asctime)-15s [%(levelname)5.5s] [%(threadName)15.15s] %(name)s#%(funcName)s:%(lineno)s %(message)s")
    if not opts.daemon:
        streamHdlr = logging.StreamHandler(sys.stdout)
        streamHdlr.setFormatter(fmt)
        logging.root.addHandler(streamHdlr)

    # This is the rotating logfile we'll use from now on
    logdir = opts.logdir
    createDirIfMissing(logdir)
    logfile = os.path.join(logdir, "dfms%s.log" % (opts.dmAcronym))
    rotatingFH = logging.handlers.RotatingFileHandler(logfile, maxBytes=10*1024*1024, backupCount=30, encoding='utf-8')
    rotatingFH.setFormatter(fmt)
    logging.root.addHandler(rotatingFH)

    # Per-package/module specific levels
    logging.root.setLevel(level)
    logging.getLogger("dfms").setLevel(level)
    logging.getLogger("tornado").setLevel(logging.WARN)
    logging.getLogger("luigi-interface").setLevel(logging.WARN)

# Entry-point function for the dfmsNM script
def dfmsNM(args=sys.argv):
    """
    Entry point for the dfmsNM command-line script, which starts a
    NodeManager and exposes it through Pyro and a REST interface.
    """

    # Parse command-line and check options
    parser = optparse.OptionParser()
    addCommonOptions(parser, NODE_DEFAULT_REST_PORT)
    parser.add_option("--no-dlm", action="store_true",
                      dest="noDLM", help="Don't start the Data Lifecycle Manager on this NodeManager", default=False)
    parser.add_option("--dfms-path", action="store", type="string",
                      dest="dfmsPath", help="Path where more dfms-related libraries can be found", default="~/.dfms/lib")
    (options, args) = parser.parse_args(args)

    # Add DM-specific options
    # Note that the host we use to expose the NodeManager itself through Pyro is
    # also used to expose the Sessions it creates
    options.dmType = NodeManager
    options.dmArgs = ()
    options.dmKwargs = {'useDLM': not options.noDLM, 'dfmsPath': options.dfmsPath, 'host': options.host}
    options.dmAcronym = 'NM'
    options.restType = NMRestServer

    start(options, parser)

def dfmsCompositeManager(args, dmType, acronym, dmPort, dmRestServer):
    """
    Common entry point for the dfmsDIM and dfmsMM command-line scripts. It
    starts the corresponding CompositeManager and exposes it through Pyro and a
    REST interface.
    """

    # Parse command-line and check options
    parser = optparse.OptionParser()
    addCommonOptions(parser, dmPort)
    parser.add_option("-N", "--nodes", action="store", type="string",
                      dest="nodes", help = "Comma-separated list of node names managed by this %s" % (acronym), default="")
    parser.add_option("-k", "--ssh-pkey-path", action="store", type="string",
                      dest="pkeyPath", help = "Path to the private SSH key to use when connecting to the nodes", default=None)
    parser.add_option("--dmCheckTimeout", action="store", type="int",
                      dest="dmCheckTimeout", help="Maximum timeout used when automatically checking for DM presence", default=10)
    (options, args) = parser.parse_args(args)

    # Add DIM-specific options
    options.dmType = dmType
    options.dmArgs = ([s for s in options.nodes.split(',') if s],)
    options.dmKwargs = {'pkeyPath': options.pkeyPath, 'dmCheckTimeout': options.dmCheckTimeout}
    options.dmAcronym = acronym
    options.restType = dmRestServer

    start(options, parser)

# Entry-point function for the dfmsDIM script
def dfmsDIM(args=sys.argv):
    """
    Entry point for the dfmsDIM command-line script.
    """
    dfmsCompositeManager(args, DataIslandManager, 'DIM', ISLAND_DEFAULT_REST_PORT, CompositeManagerRestServer)

# Entry-point function for the dfmsDIM script
def dfmsMM(args=sys.argv):
    """
    Entry point for the dfmsMM command-line script.
    """
    dfmsCompositeManager(args, MasterManager, 'MM', MASTER_DEFAULT_REST_PORT, MasterManagerRestServer)


if __name__ == '__main__':
    # If this module is called directly, the first argument must be dfmsMM,
    # dfmsNM or dfmsDIM, the rest of the arguments are the normal ones
    if len(sys.argv) == 1:
        print 'Usage: %s [dfmsNM|dfmsDIM|dfmsMM] [options]' % (sys.argv[0])
        sys.exit(1)
    dm = sys.argv.pop(1)
    if dm == 'dfmsNM':
        dfmsNM()
    elif dm == 'dfmsDIM':
        dfmsDIM()
    elif dm == 'dfmsMM':
        dfmsMM()
    else:
        print 'Usage: %s [dfmsNM|dfmsDIM|dfmsMM] [options]' % (sys.argv[0])
        sys.exit(1)