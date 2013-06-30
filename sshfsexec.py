#!/usr/bin/env python
from __future__ import print_function

import errno
import os
import pipes
import re
import sys
import stat

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
    def unescape(path):
        "Unescape octal-escaped path."
        def suboctal(match):
            "Convert octal sequence regex match to a character."
            return chr(int(match.group(0)[1:], 8))
        return re.sub("\\\\[0-7]{1,3}", suboctal, path)

    mapping = dict()
    with open("/proc/self/mountinfo") as iostream:
        for line in iostream:
            fields = line.split(' ', 11)
            fstype = fields[7]
            mountpoint = unescape(os.path.abspath(fields[4]))

            if fstype == 'fuse.sshfs':
                remote, path = fields[8].split(':', 1)
                device = os.makedev(*(map(int, fields[2].split(':'))))
                mapping[mountpoint] = (remote, os.path.abspath(unescape(path)))

            else:
                mapping[mountpoint] = None

    return mapping


def translatepath(localpath, devicemap):
    """
    Determine the remote SSH host and remote path of a file located within
    an sshfs mount point. The mountmap is a dictionary in the format
    returned by sshfsmountmap. A tuple containing the remote login
    ([user@]hostname), remote path, and local mount point for the SSHFS
    volume is returned if `localpath` is within an SSHFS mount point and
    `None` otherwise. The `localpath` does not need to exist in order for a
    translated path to be returned.
    """
    testdir = os.path.abspath(localpath)
    while True:
        mountpoint = testdir
        mountinfo = devicemap.get(mountpoint)

        if mountinfo:
            remotelogin, remoteroot = mountinfo
            break
        elif not testdir or testdir == '/':
            return None

        testdir, _ = os.path.split(testdir)

    relpath = os.path.relpath(os.path.abspath(localpath), mountpoint)
    remotepath = os.path.join(remoteroot, relpath)

    return remotelogin, remotepath, mountpoint


def main(configcode=''):
    mountmap = sshfsmountmap()
    command, originalargs = os.path.basename(sys.argv[0]), sys.argv[1:]
    envpassthrough = dict()
    environment = dict(os.environ)
    stdin_is_pipe = stat.S_ISFIFO(os.fstat(0).st_mode)

    # Configuration defaults
    translate_all_arguments = False
    preserve_isatty = False
    coerce_remote_execution = False

    # Figure out where the current working directory is on the remote system.
    cwdtranslation = translatepath(os.getcwd(), mountmap)
    if cwdtranslation:
        sshlogin, remotecwd, basemountpoint = cwdtranslation
        sshhost = sshlogin.split('@')[0] if '@' in sshlogin else sshlogin
    else:
        sshlogin = None

    # First execution of configuration code prior to processing arguments.
    pre_process_config = True
    exec(configcode)

    remoteargs = list()
    for argument in originalargs:
        translation = translatepath(argument, mountmap)

        if not translation:
            remoteargs.append(argument)
            continue

        login, transpath, argmountpoint = translation
        arghost = login.split('@')[0] if '@' in login else login

        # Paths used with coerced execution must be absolute
        if coerce_remote_execution and not cwdtranslation:
            argument = transpath

        if not sshlogin and coerce_remote_execution:
            sshlogin = login
            basemountpoint = argmountpoint
            sshhost = sshlogin.split('@')[0] if '@' in sshlogin else sshlogin
        elif sshlogin and arghost != sshhost:
            print("SSHFS host mismatch.", file=sys.stderr)
            exit(EXIT_SSHFS_HOST_MISMATCH)

        # If the argument is an absolute path or a relative path that crosses
        # over to a different SSHFS mount point, use an absolute path for the
        # remote command.
        if sshlogin and basemountpoint != argmountpoint or argument[0] == '/':
            remoteargs.append(transpath)

        else:
            if cwdtranslation:
                # Ensure the mount point is not referenced by its local name,
                # e.g. ../../mountpoint/subfolder. If is is, switch to an
                # absolute path.
                argupdirs = os.path.normpath(argument).split('/').count('..')
                highestreference = os.path.abspath(('../' * (argupdirs - 1)))
                refmount = mountmap.get(highestreference)
                if refmount:
                    remotebasename = os.path.basename(refmount[1])
                    localmountname = os.path.basename(highestreference)

                if argupdirs and refmount and remotebasename != localmountname:
                    remoteargs.append(transpath)
                    continue

            remoteargs.append(argument)

    # Second execution of configuration code after processing arguments.
    pre_process_config = False
    exec(configcode)

    if sshlogin:
        # If the command should be executed on a remote server, generate the
        # execution string to pass into the shell.
        executed = listtoshc([command] + remoteargs)

        # Prepend environment variable declarations
        for variable, value in envpassthrough.iteritems():
            executed = '%s=%s %s' % (variable, pipes.quote(value), executed)

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
            quotedremotecwd = pipes.quote(remotecwd)
            sshcommand = '{ cd %s && %s; }' % (quotedremotecwd, executed)
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

        argv = [SSH_BINARY]
        if ttyoption == '-T':
            argv += ['-e', 'none']

        argv += [sshlogin, ttyoption, sshcommand]

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

    os.execvpe(argv[0], argv, environment)


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
