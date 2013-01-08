#!/usr/bin/env python
from __future__ import print_function

import errno
import os
import pipes
import subprocess
import sys
import token
import tokenize
import collections

EXIT_COMMAND_NOT_FOUND = 127
EXIT_SSHFS_HOST_MISMATCH = 1
SSH_BINARY = 'ssh'


def which(binary, path=os.environ.get('PATH', '')):
    """
    Return the absolute path of an executable with the basename defined by
    `binary.` If an absolute path is given for `binary`, then the value is
    `binary` is returned if it is executable. In the event that an
    executable binary cannot be found, None is returned. This command is
    analogous to the *nix command "which(1)."
    """
    if '/' in binary:
        if os.access(binary, os.X_OK):
            return binary
        return None

    for folder in path.split(':'):
        try:
            contents = os.listdir(folder)
        except Exception:
            continue

        if binary in contents:
            binarypath = os.path.abspath(os.path.join(folder, binary))
            if os.access(binarypath, os.X_OK):
                return binarypath

    return None


def listtoshc(arglist):
    """
    Convert a list of command line arguments to a string that can be
    executed by POSIX shells without interpolation of special characters.

    Example:
    >>> print(listtoshc(['cat', '.profile', '`rm -rf *`']))
    cat .profile '`rm -rf *`'
    """
    return ' '.join(map(pipes.quote, arglist))


def sshfsdevicemap():
    """
    Return a dictionary mapping device ID's to tuples containing the mount
    point, remote ([user@]hostname) and path for all remote hosts mounted
    with sshfs.
    """
    mapping = dict()
    with open("/proc/self/mountinfo") as iostream:
        for line in iostream:
            fields = line.split(' ', 11)
            fstype = fields[7]

            if fstype == 'fuse.sshfs':
                mountpoint = fields[4]
                remote, path = fields[8].split(':', 1)
                device = os.makedev(*(map(int, fields[2].split(':'))))
                mapping[device] = (mountpoint, remote, path)

    return mapping


def translatepath(localpath, devicemap, relative=False, searchup=True):
    """
    Determine the remote SSH host and remote path of a file located within
    an sshfs mount point. The devicemap must be a dictionary in the format
    returned by sshfsdevicemap. When set, `relative` will yield a path
    relative to the mount point's root. With the `searchup` option set, the
    function will attempt to determine the device ID of `localpath` by
    iteratively checking the parent directories in cases where `localpath`
    does not yet exist. A tuple containing the remote host, remote path,
    and remote path of the mount point is returned.
    """
    statdir = localpath
    while True:
        try:
            device = os.stat(statdir).st_dev
            break
        except EnvironmentError as e:
            if e.errno != errno.ENOENT or not searchup:
                raise
            statdir, _ = os.path.split(statdir)

        if not statdir or statdir == '/':
            return None

    try:
        mountpoint, host, remoteroot = devicemap[device]
    except KeyError:
        return None

    localpath = os.path.join(os.getcwd(), localpath)
    relpath = os.path.relpath(localpath, mountpoint)
    if relative:
        remotepath = relpath
    else:
        remotepath = os.path.join(remoteroot, relpath)

    return host, remotepath, remoteroot


def main(configcode=''):
    sshfsmounts = sshfsdevicemap()
    command, originalargs = os.path.basename(sys.argv[0]), sys.argv[1:]
    environment = dict(os.environ)

    # Commands to execute prior to running the target command on the remote
    # system
    commandprefix = ''

    # Figure out where the current working directory is on the remote system.
    cwdtranslation = translatepath(os.getcwd(), sshfsmounts, searchup=False)
    if cwdtranslation:
        sshremote, remotecwd, execremoteroot = cwdtranslation
        commandprefix = 'cd %s &&' % (pipes.quote(remotecwd))
    else:
        sshremote = ''

    translate_all_arguments = False
    transargs = list()

    # These variables are accessible inside the configuration script's context.
    configvars = ('command', 'originalargs', 'environment', 'commandprefix',
        'cwdtranslation', 'translate_all_arguments', 'sshremote', 'transargs',
        'pre_process_config')

    # First execution of configuration code prior to processing arguments. The
    # configu script is run in its namespace of sorts. Yes, I know this is a
    # terrible hack, and I should be ashamed of myself. I _may_ say "screw the
    # isolation" and change it to just `exec configcode` at some point.
    pre_process_config = True
    configscope = dict([(k, locals()[k]) for k in configvars])
    exec(configcode, configscope)
    for key, value in configscope.iteritems():
        _ = value
        exec(key + " = _")

    for argument in originalargs:
        # Attempt to translate any of the arguments that appear to be paths for
        # SSHFS-mounted locations remote system. Anything that begins with '/'
        # or '../' or contains '/../' will be translated. When the current
        # working directory is not inside an SSHFS mount point, anything that
        # begins with './' will also be translated.
        if (translate_all_arguments or
         (any(map(argument.startswith, ('/', '../'))) or '/../' in argument)
          or (not cwdtranslation and argument.startswith('./'))):
            try:
                translation = translatepath(argument, sshfsmounts)
                if translation:
                    mounthost, remotepath, remoteroot = translation
                    if not sshremote:
                        sshremote = mounthost

                    # Verify arguments don't cross SSHFS hosts
                    if not argument.startswith('/'):
                        if sshremote and mounthost != sshremote:
                            if '@' in sshremote:
                                user, host = sshremote.split('@')
                            else:
                                user = None
                                host = sshremote

                            if not sshost.endswith('@' + host) and sshhost != host:
                                print("SSHFS host mismatch.", file=sys.stderr)
                                exit(EXIT_SSHFS_HOST_MISMATCH)

                        if cwdtranslation:
                            remotepath = os.path.relpath(remotepath, remotecwd)

                    transargs.append(remotepath)
                    continue

            # If the error is anything other than ENOENT (file does not exist),
            # raise it.
            except EnvironmentError as e:
                if e.errno != errno.ENOENT:
                    raise

        transargs.append(argument)

    # Second execution of configuration code after processing arguments.
    pre_process_config = False
    configscope = dict([(k, locals()[k]) for k in configvars])
    exec(configcode, configscope)
    for key, value in configscope.iteritems():
        _ = value
        exec(key + " = _")

    if sshremote:
        # If the command should be executed on a remote server, generate the
        # execution string to pass into the shell. If the current working
        # directory is inside an SSHFS mount, cd into the corresponding remote
        # directory first.
        executed = listtoshc([command] + transargs)

        if cwdtranslation:
            sshcommand = 'cd %s && %s' % (remotecwd, executed)
        else:
            sshcommand = executed

        # Allocate a pseudo-terminal if needed.
        if sys.stdout.isatty():
            argv = [SSH_BINARY, sshremote, '-t', sshcommand]
        else:
            argv = [SSH_BINARY, sshremote, sshcommand]

    else:
        # If the command does not interact with any SSHFS-mounted paths, run
        # the executable that the script replaced.
        path = os.environ.get('PATH', '')
        while path:
            replacedbinary = which(command, path)

            if replacedbinary:
                if os.path.samefile(__file__, replacedbinary):
                    if ':' in path:
                        _, path = path.split(':', 1)
                        continue
                else:
                    break

            print("sshfsexec: %s: command not found" % command)
            exit(EXIT_COMMAND_NOT_FOUND)

        argv = [replacedbinary] + originalargs

    # Launch subprocess, and ignore Ctrl+C.
    child = subprocess.Popen(argv, env=environment)
    while True:
        try:
            exit(child.wait())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    defaultconfigpath = os.path.expanduser('~/.sshfsexec.conf')
    configpath = os.environ.get('SSHFSEXEC_CONFIG', defaultconfigpath)

    try:
        with open(configpath) as iostream:
            configcode = iostream.read()
    except IOError as e:
        if e.errno != errno.ENOENT:
            raise
        configcode = ''

    main(configcode)
