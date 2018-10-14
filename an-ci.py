#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import yaml
import sh
import sys
import os
import getpass
import shellescape
import queue

_CI_FILE = '.ci.yaml'

def env_constructor(loader, node):
    env = {}
    env['UID'] = os.getuid()
    env['GID'] = os.getgid()
    env.update(os.environ)
    return str(node.value).format(**env)


def docker_constructor(loader, node):
    kwargs = loader.construct_mapping(node)
    return DockerTask(**kwargs)


class DockerTask(object):
    def __init__(self, image, commands, default_user=False):
        super(DockerTask, self).__init__()
        self._image = image
        self._commands = commands
        self._default_user = default_user

        self._verbose = True

    def execute(self):
        try:
            user = self._prepare_user()

            in_queue = self._make_bash()
            command = self._execute(
                'bash',
                _iter=True,
                _in=in_queue,
                _user=user,
                _tty_in=True
            )
            command_it = iter(command)
            while True:
                try:
                    for l in command_it:
                        if isinstance(l, bytes):
                            sys.stdout.buffer.write(l)
                        else:
                            sys.stdout.write(l)

                        sys.stdout.flush()
                    return 0
                except KeyboardInterrupt:
                    in_queue.put("\x03") # Ctrl-C code
        except sh.ErrorReturnCode as e:
            return e.exit_code


    def _prepare_user(self):
        if self._default_user: return None

        user_name = getpass.getuser()
        uid = os.getuid()
        gid = os.getgid()

        self._execute(
            'bash',
            _in=self._make_create_user_script(
                user_name,
                uid,
                gid
            )
        )

        return '{}:{}'.format(uid, gid)


    def _execute(
            self,
            command,
            *args,
            _iter=False,
            _in=None,
            _user=None,
            _tty_in=False
    ):
        if self._verbose:
            eprint(
                "[{}] Executing: {} {}",
                self._image,
                command,
                ' '.join(args)
            )

        docker_compose_args = ['exec']
        if not _tty_in: docker_compose_args.append('-T')

        if _user is not None:
            docker_compose_args.append('--user')
            docker_compose_args.append(_user)

        docker_compose_args.append(self._image)
        docker_compose_args.append(command)
        docker_compose_args.extend(args)

        return sh.Command('docker-compose')(
            *docker_compose_args,
            _bg_exc=not _iter,
            _iter=_iter,
            _err_to_out=True,
            _tty_in=_tty_in,
            _in=_in
        )

    def _make_create_user_script(self, user_name, uid, gid):
        return """#!/bin/bash
id -u {user_name}
if [ $? != 0 ]; then
  set -e
  groupadd --gid {gid} {user_name}
  useradd --uid {uid} --gid {gid} --create-home {user_name}
  echo "{user_name}    ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers
fi
""".format(
    user_name=user_name,
    uid=uid,
    gid=gid
)

    def _make_bash(self):
        in_queue = queue.Queue()
        in_queue.put("'set' '-e'\n")

        for command in self._commands:
            x = ' '.join(shellescape.quote(str(x)) for x in _unroll(command))
            in_queue.put("{}\n".format(x))

        in_queue.put("'exit'\n")

        return in_queue


def isolate_command_constructor(loader, node):
    command = loader.construct_sequence(node)
    return ['(', command, ')']


def and_command_constructor(loader, node):
    command = loader.construct_sequence(node)
    return ['&&', command]


def pipe_command_constructor(loader, node):
    command = loader.construct_sequence(node)
    return ['|', command]


yaml.add_constructor('!env', env_constructor)
yaml.add_constructor('!docker', docker_constructor)
yaml.add_constructor('!isolate', isolate_command_constructor)
yaml.add_constructor('!and', and_command_constructor)
yaml.add_constructor('!pipe', pipe_command_constructor)


def eprint(text, *args, **kwargs):
    if len(args) + len(kwargs) != 0:
        text = text.format(*args, **kwargs)
    sys.stderr.write(str(text) + '\n')


def execute_task(task, cwd=None):
    if isinstance(task, DockerTask):
        exit_code = call_command(['docker-compose', 'up', '-d'], cwd=cwd)
        if exit_code != 0:
            return exit_code

        return task.execute()
    else:
        for command in task:
            exit_code = call_command(command, cwd=cwd)
            if exit_code != 0:
                return exit_code

    return 0


def call_command(command, cwd=None):
    env = dict(os.environ)
    if cwd is not None:
        env['PWD'] = cwd

    command_it = _unroll(command)
    command = next(command_it)
    try:
        it = sh.Command(command)(
            *command_it,
            _bg_exc=False,
            _iter=True,
            _err_to_out=True,
            _cwd=cwd,
            _env=env
        )
        for l in it:
            sys.stdout.write(l)
            sys.stdout.flush()
    except sh.ErrorReturnCode as e:
        return e.exit_code

    return 0


def _unroll(arg):
    if isinstance(arg, list):
        for x in arg:
            for y in _unroll(x):
                yield y
    else:
        yield arg


def get_work_path():
    path = os.getcwd()
    while True:
        if os.path.isfile(os.path.join(path, _CI_FILE)):
            return path
        path, head = os.path.split(path)
        if head == '': return None


def main(argv):
    work_path = get_work_path()
    if work_path is None:
        eprint("Can't found a '{}' file", _CI_FILE)
        return -1

    with open(os.path.join(work_path, _CI_FILE)) as f:
        data = yaml.load(f)

    if len(argv) <= 1:
        eprint("Usage: an-ci <workflow>")
        eprint("")
        eprint("The available workflows are:")
        for workflow in sorted(data['workflows'].keys()):
            eprint(" - {}", workflow)

        return -1

    workflow_id = argv[1]

    eprint("* Running workflow: {} *", workflow_id)
    for task_id in data['workflows'][workflow_id]:
        eprint("\n** Running task: {} **", task_id)
        task = data['tasks'][task_id]

        exit_code = execute_task(task, cwd=work_path)
        if exit_code != 0:
            eprint("** ERROR: The task exit with a exit code: {}", exit_code)
            return 1

    return 0

if __name__ == "__main__":
    exit_code = main(sys.argv)
    sys.exit(exit_code)
