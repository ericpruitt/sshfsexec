sshfsexec
=========

Description
-----------

I use SSHFS frequently so I can work on remote servers using local
applications. The access times are generally acceptable on a modern broadband
connection for basic text editing with Vim, but I/O heavy commands like git or
grep with multiple files are insufferably slow. To resolve this, I wrote
sshfsexec which transparently executes commands on remote systems that host
locally mounted SSHFS volumes.

Despite the name, [this tool also supports NFS v3 and v4 mounts][nfs-pr], but
this was only added in December 2018 — several years after the tool was
originally created.

  [nfs-pr]: https://github.com/ericpruitt/sshfsexec/pull/4

Basic Configuration and Usage
-----------------------------

Copy sshfsexec to a folder defined in the `PATH` environment variable, and make
sure the file is executable. The folder that sshfsexec is copied to should not
contain any executable that shares the base-name of commands to be run on
remote systems for reasons that should become clear later, so it is best to
create a new folder specifically for sshfsexec and adjust the `PATH`
environment variable accordingly.

    $ mkdir ~/bin/sshfsexec
    $ cp sshfsexec.py ~/bin/sshfsexec
    $ chmod +x ~/bin/sshfsexec/sshfsexec.py
    $ export PATH="$HOME/bin/sshfsexec:$PATH"

When adjusting the `PATH` environment variable, the login profile should also
be edited (generally `~/.profile`) so `PATH` is setup automatically upon login.
Once sshfsexec has been copied into a folder in `PATH`, create symlinks from
sshfsexec for the commands that should be executed transparently on the remote
systems. In the example below, `git` will be executed on remote systems when
interacting with SSHFS volumes.

    $ cd ~/bin/sshfsexec
    $ ln -s sshfsexec.py git
    $ hash -r                     # Clears the executable path cache in Bash.

Any time `git` is launched inside of the SSHFS volume, it will be executed on
the remote system transparently:

    ~$ sshfs codevat.com:/home/git/repositories git.codevat.com/
    ~$ cd git.codevat.com/mydwm.git
    mydwm.git$ git log | head
    commit 8f80c5343a8b430b28d18a6902ed2fbb05a6c90a
    Author: Eric Pruitt
    Date:   Wed Dec 26 19:53:45 2012 -0600

        Folder paths normalized in dwmstatus

        - Folder paths may now end with slashes, and repeated slashes are
          normalized to a single slash.

    commit bfd709906a5a5cc0f3788e342bbc47d7e6aa5a92
    mydwm.git$

That is not a particularly convincing example. Let us compare `git --version`
executed inside the SSHFS volume and the local home directory:

    mydwm.git$ git --version
    git version 1.7.1
    Connection to codevat.com closed.
    mydwm.git$ cd
    ~$ git --version
    git version 1.7.2.5

Whenever a command should be executed locally instead of remotely, sshfsexec
will traverse the folders in `PATH` looking for an executable with the same
name that was used to invoke sshfsexec. Once an executable is found, it is
launched with the arguments originally passed to sshfsexec.

Tips, Tricks and Ideas
----------------------

### Improving Performance ###

With the default SSH client configuration, a new SSH connection to the remote
server must be created each time a command is executed. Using the configuration
options `ControlMaster` and `ControlPath` to setup shared SSH connections will
reduce the latency of command invocation on remote servers. Here is a
comparison of command execution time with and without SSH connection sharing:

    # SSH connection sharing not enabled
    codevat$ time grep &> /dev/null
    real    0m0.927s

    # Unmounting remote server and enabling SSH connection sharing
    codevat$ cd
    ~$ fusermount -u codevat/
    ~$ vi ~/.ssh/config
    ~$ sshfs codevat.com:/ codevat/

    # SSH connection sharing enabled
    ~$ cd codevat/
    codevat$ time grep &> /dev/null
    real    0m0.135s

To enable SSH connection sharing, add the following lines to the local SSH
configuration at `~/.ssh/config`:

    ControlMaster auto
    ControlPath /tmp/ssh_mux_%h_%p_%r

Next, unmount and remount the SSHFS volume to begin using connection sharing.
Check out the documentation in ssh_config(5) for more information on SSH
connection sharing.

### SSH Connection Messages ###

SSH will display a message like "Connection to $HOSTNAME closed." or "Shared
connection to $HOSTNAME closed." whenever an SSH session with a pseudo-terminal
terminates. Although this message can be disabled with `Loglevel=quiet`, doing
so also disables at least one critical security message as noted by a commenter
in [OpenSSH bug #1273](https://bugzilla.mindrot.org/show_bug.cgi?id=1273#c6). I
found these messages annoying, but I was unwilling to accept the possibility of
suppressing important notices. I patched my OpenSSH client binary, changing the
first character of each format string to a null byte, to get rid of the
messages. I used `xxd` and `vim` the first time I patched the binary, but
provided the format strings in the targeted version of OpenSSH do not differ
from mine, the following perl substitution should work just as well:

    $ ssh -V
    OpenSSH_5.5p1 Debian-6+squeeze2, OpenSSL 0.9.8o 01 Jun 2010
    $ strings /usr/bin/ssh | egrep -i '(Shared )?connection to \S+ closed\.'
    Connection to %.64s closed.
    Shared connection to %s closed.
    $ cp /usr/bin/ssh ~/bin
    $ perl -pi -e 's/((Shared )?connection to \S+ closed)\./\0\1/ig' ~/bin/ssh

### Usage Ideas ###

Here are some of the most frequently used programs I have symlinked to
sshfsexec:

    crontab  egrep  fgrep  find  git  grep  last  mysql  php  service  w  wget

Advanced Configuration
----------------------

Configuration is managed with a Python script read from `~/.sshfsexec.conf` or
the path defined by the environment variable `SSHFSEXEC_CONFIG`. This script
will be executed twice, once before the command arguments are translated and
once after. On the first pass, `pre_process_config` is `True` and `False` on
the second pass. During the first execution, `remoteargs` will be unpopulated,
and `sshlogin` will not yet be set if sshfsexec was run outside of an SSHFS
volume. A sample configuration script named "config-sample.py" is included with
the source code to sshfsexec.

When the configuration script is executed, it is executed inside of sshfsexec's
`main` function with unrestricted access to the code, but a list of the most
relevant variables follows below.

### Options ###

**pre_process_config**: Variable indicating whether or not the configuration
file is being executed before or after the command arguments have been
translated to remote paths.

**command**: Base-name of the command being executed
(`os.path.basename(sys.argv[0])`) when the sshfsexec is launched.

**coerce_remote_execution**: Determines whether or not referencing a path
within an SSHFS volume will coerce a command to be executed remotely instead of
locally. When this option is set, if a command's arguments reference paths that
are within an SSHFS volume, one's current working directory need not be inside
the mount for transparent usage. In the following example, git would be
executed remotely with `coerce_remote_execution` set:

    ~$ git clone ~/git.codevat.com/mydwm.git ~/git.codevat.com/clone.git
    Initialized empty Git repository in /home/git/repositories/clone.git/.git/

This option is disabled by default and must be set before the command arguments
are parsed / while `pre_process_config` is `True`.

**preserve_isatty**: A PTY will normally only be allocated on the remote server
if the local stdin and stdout are pseudo-terminals. This causes problems with
things like `grep --color` and `ls --color=auto`, both of which check the value
of isatty on stdout to determine whether or not colors should be rendered.
Setting `preserve_isatty` will result in some kludgey stuff being done to make
sure the isatty results for stdin, stdout and stderr on the remote process are
the same as for the local sshfsexec process.

In its current implemenation, this option is buggy: anything launched on the
remote server that reads from stdin will hang indefinitely even when no more
data is available and the local pipe has been closed.

### Command Execution Variables ###

**environment**: A dictionary containing the environment variables that will be
passed to the subprocess. Note that for commands run via SSH, the environment
variables are set locally before SSH is launched. This means that environment
variables are subject to the configuration settings on the remote host,
specifically `PermitUserEnvironment`.

**envpassthrough**: This dictionary is used to configure environment variables
on the remote server. Normally, only environment variables permitted by the
remote server's sshd configuration will be made available to the remote
process, but with `envpassthrough`, the environment variables will be declared
on the remote server before executing the commands. For example, if
`envpassthrough = { "EDITOR": "vim" }`, the remote invocation of `git commit`
will be as follows: `EDITOR=vim git commit`. Please note that the definition of
these environment variables is likely to be visible to all users on both the
remote server and local host, so `envpassthrough` should not be used for
sensitive information.

**stdin_is_pipe**: Boolean indicating whether or not stdin is a pipe.

**originalargs**: Iterable containing the untranslated arguments passed to the
command (`sys.argv[1:]`).

**sshlogin**: Remote SSH system where a command will be executed. This will be
in the form of `user@hostname` or simply `hostname` depending on the arguments
used for `sshfs` and the defined SSH options.

**cwdtranslation**: When a command is launched inside a directory within an
SSHFS mount, `cwdtranslation` is a tuple that contains the remote login
("user@hostname" or "hostname"), the remote directory that corresponds to the
current working directory, and the SSHFS local mount point for SSHFS volume. If
a command is launched outside of an SSHFS folder, this variable will be `None`.

**remoteargs**: Arguments that will be used to execute the command on the
remote system. If any of the original arguments were paths on the remote
system, the path in `remoteargs` will be the path as it exists on the remote
server instead of how it was referenced within the SSHFS mount.

To Do
-----

- Allow commands with both local and remote path arguments to be executed on
  both systems. For example `rm here.jpg ~/mounts/sshfs/there.jpg` would run
  `rm here.jpg` locally and `ssh user@remote 'rm there.jpg'`.
- Fix hanging issue with `preserve_isatty`.
