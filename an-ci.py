#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import yaml
import sh
import sys
import os

def env_constructor(loader, node):
    env = {}
    env['UID'] = os.getuid()
    env['GID'] = os.getgid()
    env.update(os.environ)
    return str(node.value).format(**env)


def docker_constructor(loader, node):
    return [
        'docker-compose',
        'exec',
        '-T',
        '--user',
        '{}:{}'.format(os.getuid(), os.getgid()),
        node.value
    ]

yaml.add_constructor('!env', env_constructor)
yaml.add_constructor('!docker', docker_constructor)


def eprint(text, *args, **kwargs):
    if len(args) + len(kwargs) != 0:
        text = text.format(*args, **kwargs)
    sys.stderr.write(str(text) + '\n')


def call_command(command):
    command_it = _unroll(command)
    command = next(command_it)
    for l in sh.Command(command)(*command_it, _bg=True, _iter=True, _err_to_out=True):
        sys.stdout.write(l)


def _unroll(args):
    for arg in args:
        if isinstance(arg, list):
            for x in _unroll(arg): yield x
        else:
            yield arg


def main(argv):
    workflow_id = argv[1]

    with open('.ci.yaml') as f: data = yaml.load(f)

    eprint("* Running workflow: {} *", workflow_id)
    for task_id in data['workflows'][workflow_id]:
        eprint("** Running task: {} **", task_id)
        task = data['tasks'][task_id]
        for command in task:
            call_command(command)


if __name__ == "__main__":
    main(sys.argv)
