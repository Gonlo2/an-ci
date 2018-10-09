#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import yaml
import sh
import sys
import os
import getpass
import shellescape

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
    def __init__(self, image, commands):
        super(DockerTask, self).__init__()
        self._image = image
        self._commands = commands

        self._verbose = True

    def execute(self):
        try:
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

            command = self._execute(
                'bash',
                _iter=True,
                _in=self._make_bash(),
                _user='{}:{}'.format(uid, gid)
            )
            command_it = iter(command)
            parent_pid = int(next(command_it))
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
                    self._execute(
                        'kill',
                        '-9',
                        '-{}'.format(parent_pid)
                    )
        except sh.ErrorReturnCode as e:
            return e.exit_code

    def _execute(
            self,
            command,
            *args,
            _iter=False,
            _in=None,
            _user=None,
    ):
        if self._verbose:
            eprint(
                "[{}] Executing: {} {}",
                self._image,
                command,
                ' '.join(args)
            )

        docker_compose_args = ['exec', '-T']

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
        commands = [
            ' '.join(shellescape.quote(str(x)) for x in _unroll(command))
            for command in self._commands
        ]

        return """#!/bin/bash
echo $$
sleep 1
set -e{verbose}

{command}""".format(
    verbose='x' if self._verbose else '',
    command='\n'.join(commands)
)


def isolate_command_constructor(loader, node):
    command = loader.construct_sequence(node)
    return ['(', command, ')']


def and_command_constructor(loader, node):
    command = loader.construct_sequence(node)
    return ['&&', command]


yaml.add_constructor('!env', env_constructor)
yaml.add_constructor('!docker', docker_constructor)
yaml.add_constructor('!isolate', isolate_command_constructor)
yaml.add_constructor('!and', and_command_constructor)


def eprint(text, *args, **kwargs):
    if len(args) + len(kwargs) != 0:
        text = text.format(*args, **kwargs)
    sys.stderr.write(str(text) + '\n')


def execute_task(task):
    if isinstance(task, DockerTask):
        return task.execute()
    else:
        for command in task:
            exit_code = call_command(command)
            if exit_code != 0:
                return exit_code

    return 0


def call_command(command):
    command_it = _unroll(command)
    command = next(command_it)
    try:
        for l in sh.Command(command)(*command_it, _bg_exc=False, _iter=True, _err_to_out=True):
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


def main(argv):
    workflow_id = argv[1]

    with open('.ci.yaml') as f: data = yaml.load(f)

    eprint("* Running workflow: {} *", workflow_id)
    for task_id in data['workflows'][workflow_id]:
        eprint("\n** Running task: {} **", task_id)
        task = data['tasks'][task_id]

        exit_code = execute_task(task)
        if exit_code != 0:
            eprint("** ERROR: The task exit with a exit code: {}", exit_code)
            return 1

    return 0

if __name__ == "__main__":
    exit_code = main(sys.argv)
    sys.exit(exit_code)
