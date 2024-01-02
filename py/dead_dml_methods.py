# © 2024 Intel Corporation
# SPDX-License-Identifier: MPL-2.0

'''This module provides functions for finding dead DML methods based on
DMLC-generated C code. The idea is that if no device in a code
repository generates C code with a #line directive pointing into a
particular method, then that method is dead within that
repository. If that method is defined within the same repository,
then it should either be removed or tested.

Dead method analysis is useful as a complement to code coverage tools,
which detect what parts of DMLC-generated code that is never run; dead
methods are excluded from this analysis since no C code is generated
for them.

The exposed API is considered to be internal, but is small and simple
so is unlikely to change often.

Example usage (note that some additional filtering is needed for usability):

c_files = set(Path('linux64/obj/modules').glob('*/*-dml.c'))
(dead, skipped) = find_dead_methods(
    c_files, set().union(*(dml_sources(c_file) for c_file in c_files)))
for (file, lines) in dead.items():
    for (line, name) in lines:
        print(f'{file}:{line}: warning: dead method: {name}')

'''

from pathlib import Path
import re
import math

__all__ = ('dml_sources', 'find_dead_methods')

c_dml_header_re = re.compile(r'''/\*
 \* Generated by dmlc, do not edit!
 \*
 \* Source files:
((?: \*   .*
)*) \*/''')

def dml_sources_from_body(body):
    return [line[5:] for line in c_dml_header_re.match(body).group(1).splitlines()]


assert dml_sources_from_body('''\
/*
 * Generated by dmlc, do not edit!
 *
 * Source files:
 *   /a/b.dml
 *   /c/d.dml
 */
blah blah''') == ['/a/b.dml', '/c/d.dml']


def dml_sources(c_file: Path) -> set[Path]:
    """Given a DMLC-generated C file, return the set of DML files that
    were used to generate it"""
    for p in map(Path, dml_sources_from_body(c_file.read_text())):
        assert p.is_absolute(), f'{p} is not an absolute path'
        yield p


def traverse_ast(ast):
    if ast.kind in {
            'constant', 'dml_typedef', 'extern', 'extern_typedef',
            'loggroup', 'struct', 'import', 'header', 'footer', 'is',
            'typedparam', 'parameter', 'saved', 'session',
            'sharedhook', 'hook', 'error', 'export'}:
        return
    elif ast.kind == 'dml':
        (_, stmts) = ast.args
        for stmt in stmts:
            yield from traverse_ast(stmt)
    elif ast.kind == 'object':
        (_, _, _, stmts) = ast.args
        for stmt in stmts:
            yield from traverse_ast(stmt)
    elif ast.kind == 'method':
        # filter out inline methods: an inline method may be
        # completely optimized out
        ignored = False
        (inp, _, _, _, body) = ast.args[1]
        for (_, _, _, typ) in inp:
            if typ is None:
                ignored = True
        assert body.kind == 'compound'
        if any(stmt.kind == 'error' for stmt in body.args[0]):
            # poisoned method, apparently meant to be dead
            ignored = True
        yield (ast.site.lineno, ast.args[4].lineno, ast.args[0], ignored)
    elif ast.kind == 'sharedmethod':
        body = ast.args[6]
        if body is None:
            # abstract method, no code generated
            return
        assert body.kind == 'compound'
        yield (ast.site.lineno, ast.args[7].lineno, ast.args[0], False)
    elif ast.kind in {'toplevel_if', 'hashif'}:
        (_, t, f) = ast.args
        for block in [t, f]:
            for stmt in block:
                yield from traverse_ast(stmt)
    else:
        assert ast.kind in {'template', 'template_dml12', 'in_each'}, ast.kind
        (_, body) = ast.args
        for stmt in body:
            yield from traverse_ast(stmt)

def method_locations(path):
    from dml.toplevel import parse_file, determine_version
    from dml import logging, messages
    for warning in messages.warnings:
        logging.ignore_warning(warning)

    (version, _) = determine_version(path.read_text(), path)
    ast = parse_file(path)
    if version == (1, 2):
        # ignore dead methods in DML 1.2: inlining patterns in DML 1.2
        # cause too many false positives
        return [(start, stop, name, True)
                for (start, stop, name, _) in traverse_ast(ast)]
    else:
        return list(traverse_ast(ast))


line_directive_re = re.compile('^ *#line ([0-9]+) "(.*)"$', flags=re.M)

assert line_directive_re.search('''
foo
   #line 109 "foo.dml"
   bar''').groups() == ('109', 'foo.dml')


def find_dead_methods(c_files: set[Path], dml_files: set[Path]) -> (
        dict[Path, list[int]], list[Path]):
    '''Given a set of DMLC-generated C files and a set of DML files,
    analyze #line directives in the C files and return a pair `(dead,
    skipped)`, where `dead` lists the dead methods among these DML
    files, and `skipped` is the set of files for which analysis was
    skipped: files that were not included in `dml_files` but for which
    #line directives were found.
    '''
    linemarks_by_path : dict[Path, set(int)]= {}
    for c_file in sorted(c_files):
        linemarks_by_pathstr : dict[str, list[int]] = {}
        for match in line_directive_re.finditer(c_file.read_text()):
            (line_str, dml_file) = match.groups()
            linemarks_by_pathstr.setdefault(
                dml_file, []).append(int(line_str))
        # normalize method filenames, possibly merging line lists
        for (dml_file, linemarks) in linemarks_by_pathstr.items():
            abs_path = (c_file.parent / dml_file).resolve()
            # disregard self-referencing `#line 4711 "foo-dml.c"`
            # directives
            if abs_path != c_file:
                linemarks_by_path.setdefault(abs_path, set()).update(linemarks)
    skipped : list[Path] = []
    dead : dict[Path, list[int]] = {}
    for (dml_file, linemarks) in linemarks_by_path.items():
        if dml_file in dml_files:
            linemarks = sorted(linemarks) + [math.inf]
            i = 0
            for (first_line, last_line, name, ignored) in sorted(
                    method_locations(dml_file)):
                if linemarks[i] > last_line and not ignored:
                    dead.setdefault(dml_file, []).append((first_line, name))
                while linemarks[i] <= last_line:
                    i += 1
        else:
            skipped.append(dml_file)
    # so far we only found dead methods in files referenced by #line
    # directives; also cover DML files that *only* contain dead
    # methods.
    for path in dml_files.difference(linemarks_by_path):
        dead[path] = [
            (first_line, name)
            for (first_line, _, name, ignore) in method_locations(path)
            if not ignore]
    return (dead, skipped)
