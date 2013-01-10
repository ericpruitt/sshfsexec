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


def sshfsmountmap():
    """
    Return a dictionary mapping mount points to tuples containing the
    remote login ([user@]hostname) and remote path for all remote hosts
    mounted with sshfs.
    """
    mapping = dict()
    with open("/proc/self/mountinfo") as iostream:
        for line in iostream:
            fields = line.split(' ', 11)
            fstype = fields[7]
            mountpoint = os.path.abspath(fields[4])

            if fstype == 'fuse.sshfs':
                remote, path = fields[8].split(':', 1)
                device = os.makedev(*(map(int, fields[2].split(':'))))
                mapping[mountpoint] = (remote, os.path.abspath(path))

            else:
                mapping[mountpoint] = None

    return mapping


def translatepath(localpath, devicemap):
    """
    Determine the remote SSH host and remote path of a file located within
    an sshfs mount point. The mountmap is a dictionary in the format
    returned by sshfsmountmap. A tuple containing the remote login
    ([user@]hostname), remote path, and remote path of the mount point is
    returned `localpath` is within an SSHFS mount point, even if the file
    does not exist, and `None` otherwise.
    """
    testdir = os.path.abspath(localpath)
    while True:
        mountpoint = testdir
        mountinfo = devicemap.get(mountpoint, None)

        if mountinfo:
            host, remoteroot = mountinfo
            break
        elif not testdir or testdir == '/':
            return None

        testdir, _ = os.path.split(testdir)

    localpath = os.path.join(os.getcwd(), localpath)
    relpath = os.path.relpath(localpath, mountpoint)
    remotepath = os.path.join(remoteroot, relpath)

    return host, remotepath, remoteroot


def main(configcode=''):
    mountmap = sshfsmountmap()
    command, originalargs = os.path.basename(sys.argv[0]), sys.argv[1:]
    environment = dict(os.environ)

    # Commands to execute prior to running the target command on the remote
    # system
    commandprefix = ''

    # Figure out where the current working directory is on the remote system.
    cwdtranslation = translatepath(os.getcwd(), mountmap)
    if cwdtranslation:
        sshremote, remotecwd, execremoteroot = cwdtranslation
        commandprefix = 'cd %s &&' % (pipes.quote(remotecwd))
    else:
        sshremote = ''

    translate_all_arguments = False
    preserve_isatty = False
    transargs = list()

    # These variables are accessible inside the configuration script's context.
    configvars = ('command', 'originalargs', 'environment', 'commandprefix',
        'cwdtranslation', 'translate_all_arguments', 'sshremote', 'transargs',
        'pre_process_config', 'preserve_isatty')

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
                translation = translatepath(argument, mountmap)
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
        # execution string to pass into the shell.
        executed = listtoshc([command] + transargs)

        if cwdtranslation:
            # If the current working directory is inside an SSHFS mount, cd
            # into the corresponding remote directory first. Why the brackets?
            # When data is piped into cd without cd being in a command group,
            # cd will not work:
            #
            #   ~% echo example | cd / && pwd
            #   /home/jameseric
            #   ~% echo example | { cd / && pwd; }
            #   /
            #
            sshcommand = '{ cd %s && %s; }' % (remotecwd, executed)
        else:
            sshcommand = executed

        ttys = [fd.isatty() for fd in (sys.stdin, sys.stdout, sys.stderr)]
        if any(ttys):
            ttyoption = '-t'
            if not preserve_isatty:
                # Only create a tty if stdin and stdout are attached a tty.
                ttyoption = '-t' if all(ttys[0:2]) else '-T'

            elif not all(ttys):
                # Do some kludgey stuff to make isatty for the remote process
                # match the what sshfsexec sees.
                if not ttys[0]:
                    sshcommand = 'stty -echo; /bin/cat | ' + sshcommand
                    ttyoption = '-tt'
                if not ttys[1]:
                    sshcommand += ' | /bin/cat'
                if not ttys[2]:
                    sshcommand = ('exec 3>&1; %s 2>&1 >&3 3>&- | /bin/cat >&2'
                        % sshcommand)

        else:
            ttyoption = '-T'

        argv = [SSH_BINARY, '-e', 'none', sshremote, ttyoption, sshcommand]

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
