#!/usr/bin/env python
#
#                    _oo0oo_
#                   088888880
#                   88" . "88
#                   (| -_- |)
#                    0\ = /0
#                 ___/'---'\___
#               .' \\\\|     |// '.
#              / \\\\|||  :  |||// \\
#             /_ ||||| -:- |||||- \\
#            |   | \\\\\\  -  /// |   |
#            | \_|  ''\---/''  |_/ |
#            \  .-\__  '-'  __/-.  /
#          ___'. .'  /--.--\  '. .'___
#       ."" '<  '.___\_<|>_/___.' >'  "".
#      | | : '-  \'.;'\ _ /';.'/ - ' : | |
#      \  \ '_.   \_ __\ /__ _/   .-' /  /
#  ====='-.____'.___ \_____/___.-'____.-'=====
#                    '=---='
#
import os
import sys
import re
import struct
from uuid import uuid4
from ctypes import *

class Insn(object):
    def __init__(self, address, bytes, mnemonic, op_str):
        self.address = address
        self.bytes = bytes
        self.mnemonic = mnemonic
        self.op_str = op_str

    def __len__(self):
        return len(self.bytes)

    def __str__(self):
        return '%x: %s %s' % (self.address, self.mnemonic, self.op_str)

class Elf32_Ehdr(Structure):
    _fields_ = [
            ('e_ident', c_char * 16),
            ('e_type', c_ushort),
            ('e_machine', c_ushort),
            ('e_version', c_uint),
            ('e_entry', c_uint),
            ('e_phoff', c_uint),
            ('e_shoff', c_uint),
            ('e_flags', c_uint),
            ('e_ehsize', c_ushort),
            ('e_phentsize', c_ushort),
            ('e_phnum', c_ushort),
            ('e_shentsize', c_ushort),
            ('e_shnum', c_ushort),
            ('e_shstrndx', c_ushort)]

class Elf32_Shdr(Structure):
    _fields_ = [
            ('sh_name', c_uint),
            ('sh_type', c_uint),
            ('sh_flags', c_uint),
            ('sh_addr', c_uint),
            ('sh_offset', c_uint),
            ('sh_size', c_uint),
            ('sh_link', c_uint),
            ('sh_info', c_uint),
            ('sh_addralign', c_uint),
            ('sh_entsize', c_uint)]

class Elf32_Sym(Structure):
    _fields_ = [
            ('st_name', c_uint),
            ('st_value', c_uint),
            ('st_size', c_uint),
            ('st_info', c_uint8),
            ('st_other', c_uint8),
            ('st_shndx', c_ushort)]

class Elf32_Rel(Structure):
    _fields_ = [
            ('r_offset', c_uint),
            ('r_info', c_uint)]

class Elf32(bytearray):
    def __init__(self, binary):
        bytearray.__init__(self, binary)
        self.ehdr = Elf32_Ehdr.from_buffer(self)
        self.shdrs = (self.ehdr.e_shnum*Elf32_Shdr).from_buffer(self, self.ehdr.e_shoff)
        _shstrtab = self.shdrs[self.ehdr.e_shstrndx]
        self.shstrtab = (_shstrtab.sh_size*c_char).from_buffer(self, _shstrtab.sh_offset)

    def __setitem__(self, q, value):
        offset, ctype = q if type(q) is tuple else (q, c_char)
        ctype.from_buffer(self, offset).value = value

    def __getitem__(self, q):
        offset, ctype = q if type(q) is tuple else (q, c_char)
        return ctype.from_buffer(self, offset).value

    def __getslice__(self, offset, end):
        length = min(end, len(self)) - offset
        return (length*c_char).from_buffer(self, offset).raw

    def __setslice__(self, offset, end, value):
        assert end == sys.maxint
        (len(value)*c_char).from_buffer(self, offset).raw = value

    def __call__(self, name, ctype=None):
        '''return section header or section if its type is specified'''
        sh = next((sh for sh in self.shdrs \
                if string_at(self.shstrtab[sh.sh_name:]) == name), None)
        if not sh:
            return [] if ctype else None
        return sh if not ctype else \
                (sh.sh_size/sizeof(ctype)*ctype).from_buffer(self, sh.sh_offset)

    def addr2off(self, addr):
        assert self.ehdr.e_type == 2, 'not an executable file?'
        sh = next(sh for sh in self.shdrs \
                if 0 < sh.sh_addr <= addr < sh.sh_addr + sh.sh_size)
        return addr - sh.sh_addr + sh.sh_offset

    def new(self, name, sh_type=0, sh_flags=0, sh_link=0, sh_info=0, \
            sh_addralign=1, sh_entsize=0):
        '''create an empty section'''
        assert self.ehdr.e_type == 1, 'not an object file?'
        # update later sections
        for sh in self.shdrs:
            if sh.sh_offset <= self.ehdr.e_shoff:
                continue
            sh.sh_offset += sizeof(Elf32_Shdr)
        # figure out offset for the new section
        last_shdr = max(self.shdrs, key=lambda sh: sh.sh_offset)
        sh_offset = last_shdr.sh_offset + last_shdr.sh_size
        # create section header
        sh = Elf32_Shdr(sizeof(self.shstrtab), sh_type, sh_flags, 0, \
                sh_offset, 0, sh_link, sh_info, sh_addralign, sh_entsize)
        # update elf header
        self.ehdr.e_shnum += 1
        # prepare insertions
        sep = self.ehdr.e_shoff + self.ehdr.e_shentsize * (self.ehdr.e_shnum - 1)
        binary = str(self)
        self.__init__(''.join((binary[:sep],
                               str(buffer(sh)),
                               binary[sep:])))
        # append section name to shstrtab
        self.append(self.shdrs[self.ehdr.e_shstrndx], '%s\x00' % name)

    def replace(self, sh, data):
        '''replace the specified section with data'''
        assert self.ehdr.e_type == 1, 'not an object file?'
        data = str(buffer(data))
        # update sh_size
        orig_size = sh.sh_size
        sh.sh_size = len(data)
        # update other sections
        for s in self.shdrs:
            if s.sh_name == sh.sh_name:
                continue # skip the same one
            if s.sh_offset < sh.sh_offset:
                continue # skip earlier ones
            if s.sh_offset == sh.sh_offset and not s.sh_size:
                continue # skip parallel empty ones
            s.sh_offset += sh.sh_size - orig_size
        # update section header table offset
        if self.ehdr.e_shoff >= sh.sh_offset:
            self.ehdr.e_shoff += sh.sh_size - orig_size
        # replace binary
        binary = str(self)
        self.__init__(''.join((binary[:sh.sh_offset],
                               data,
                               binary[sh.sh_offset+orig_size:])))

    def append(self, sh, data):
        '''append arbitrary data to the specified section'''
        data = buffer(self, sh.sh_offset, sh.sh_size) + buffer(data)
        self.replace(sh, data)

    def disasm(self):
        '''disassemble the current binary'''
        tmpfile = '.%s.o' % uuid4()
        with open(tmpfile, 'wb') as f:
            f.write(str(self))
        insns = []
        for l in os.popen('%sobjdump -d %s' % (os.environ.get('GCCPREFIX', ''), tmpfile)):
            r = re.search(r'([0-9a-f]+):\s*(([0-9a-f]{2} )+)\s*([a-z]*)\s*([^\s]*)', l)
            if not r:
                continue
            ad, bs = int(r.group(1), 16), r.group(2).replace(' ', '').decode('hex')
            mn, op = r.group(4), r.group(5)
            if not mn:
                insns[-1].bytes += bs
            else:
                insns.append(Insn(ad, bs, mn, op))
        os.unlink(tmpfile)
        return insns

    def _branch_updates(self, new_iaddr, new_target):
        '''get branch updates given functions to compute new instruction
        address and new branch target address'''
        _text = self('.text')
        ups = []
        for i in self.disasm():
            if i.mnemonic != 'call' and not i.mnemonic.startswith('j'):
                continue # filter out non branches
            if i.op_str.startswith('*'):
                continue # filter out indirect branches
            ctype = c_int8 if len(i) == 2 else c_int
            opnd_text_off = i.address + len(i) - sizeof(ctype)
            for r in self('.rel.text', Elf32_Rel):
                if opnd_text_off == r.r_offset: # skip relocation entries
                    break
            else: # a true direct CALL/JMP
                tgt = i.address + len(i) + self[_text.sh_offset+opnd_text_off, ctype]
                tgt = new_target(tgt)
                iaddr = new_iaddr(i.address)
                new_off = tgt - iaddr - len(i)
                ups.append(dict(i=i, ctype=ctype, new_off=new_off))
        return ups

    def _update_misc(self, new_iaddr, new_target):
        '''update relocations, text symbols, ELF header and section headers'''
        syms = self('.symtab', Elf32_Sym)
        _text = self('.text')
        tshndx = (addressof(_text) - addressof(self.shdrs)) / sizeof(Elf32_Shdr)
        # update relocation entries
        for sh in self.shdrs:
            if sh.sh_type != 9: # SHT_REL
                continue
            rels = (sh.sh_size/sizeof(Elf32_Rel)*Elf32_Rel).from_buffer(self, sh.sh_offset)
            for r in rels:
                s = syms[r.r_info>>8]
                # update relocation entries referring .text section (R_386_32 and .text)
                if r.r_info & 0xff == 1 and s.st_info & 0xf == 3 and s.st_shndx == tshndx:
                    addend = self[self.shdrs[sh.sh_info].sh_offset+r.r_offset, c_uint]
                    self[self.shdrs[sh.sh_info].sh_offset+r.r_offset, c_uint] = new_target(addend)
                # update offsets of relocation entries of .text section
                if sh.sh_info == tshndx:
                    r.r_offset = new_iaddr(r.r_offset)
        # update symbols of .text section
        for s in syms:
            if s.st_shndx != tshndx:
                continue
            s.st_value = new_target(s.st_value)
            s.st_size = 0
        # calculate number of additional bytes
        more = new_iaddr(_text.sh_size) - _text.sh_size
        # update section header table offset
        self.ehdr.e_shoff += more
        # update text section header
        _text.sh_size += more
        # update later sections
        for sh in self.shdrs:
            if sh.sh_offset <= _text.sh_offset:
                continue
            sh.sh_offset += more

    def insert(self, *off_and_payload):
        '''insert multiple sequences of instructions specified by
        off_and_payload = [(off_in_text, payload), ...]'''
        assert self.ehdr.e_type == 1, 'not an object file?'
        if not off_and_payload:
            return
        off_and_payload = sorted(off_and_payload)
        _text = self('.text')
        assert _text, 'no .text section?'
        new_iaddr = lambda iaddr: iaddr + sum([len(payload) if iaddr >= off else 0 \
                for off, payload in off_and_payload])
        # a branch to off_in_text will now jump to the inserted instructions
        new_target = lambda tgt: tgt + sum([len(payload) if tgt > off else 0 \
                for off, payload in off_and_payload])
        # update .text section in place
        bups = self._branch_updates(new_iaddr, new_target)
        overflows = filter(lambda b: not -1 << sizeof(b['ctype']) * 8 - 1 <=
                b['new_off'] < 1 << sizeof(b['ctype']) * 8 - 1, bups)
        assert not overflows, 'a short JMP overflows'
        for bu in bups:
            opnd_off = _text.sh_offset + bu['i'].address + len(bu['i']) - sizeof(bu['ctype'])
            self[opnd_off, bu['ctype']] = bu['new_off']
        # update relocations, text symbols, ELF header, section headers
        self._update_misc(new_iaddr, new_target)
        # update binary
        binary = str(self)
        pieces = []
        for i in range(len(off_and_payload)):
            start = 0 if i == 0 else _text.sh_offset + off_and_payload[i-1][0]
            end = _text.sh_offset + off_and_payload[i][0]
            pieces.append(binary[start:end]+off_and_payload[i][1])
        pieces.append(binary[_text.sh_offset+off_and_payload[-1][0]:])
        self.__init__(''.join(pieces))

    def flatten(self):
        '''convert all short JMPs to near JMPs'''
        assert self.ehdr.e_type == 1, 'not an object file?'
        _text = self('.text')
        assert _text, 'no .text section?'
        # collect all short JMPs
        sjs = filter(lambda i: i.mnemonic.startswith('j') and \
                i.op_str[0] != '*' and len(i) == 2, self.disasm())
        if not sjs:
            return
        assert not filter(lambda i: i.mnemonic in ('jcxz', 'jecxz'), sjs), \
                'JCXZ and JECXZ are unsupported' # no way to flatten them
        # for computing new instruction address according to JMP opcodes
        new_iaddr = lambda addr: addr + sum([0 if j.address >= addr else \
                3 if j.bytes[0] == '\xeb' else 4 \
                for j in sjs])
        # see http://pdos.csail.mit.edu/6.828/2012/readings/i386/JMP.htm
        # and http://pdos.csail.mit.edu/6.828/2012/readings/i386/Jcc.htm
        # for conversion rules
        new_insn = lambda i, new_off: ('\xe9' if i.bytes[0] == '\xeb' else \
                ('\x0f%s' % chr(ord(i.bytes[0])+0x10))) + struct.pack('i', new_off)
        # update .text section
        ups = []
        for bu in self._branch_updates(new_iaddr, new_iaddr):
            if bu['ctype'] == c_int: # near JMP
                opnd_off = _text.sh_offset + bu['i'].address + len(bu['i']) - sizeof(c_int)
                self[opnd_off, c_int] = bu['new_off']
            else: # short JMP
                bu['new_off'] -= (5 if bu['i'].bytes[0] == '\xeb' else 6) - 2
                ups.append((bu['i'], bu['new_off']))
        assert len(ups) == len(sjs), 'miss any short JMP?'
        # update miscellaneous locations within the object
        self._update_misc(new_iaddr, new_iaddr)
        # update binary
        binary = str(self)
        pieces = []
        for i in range(len(sjs)):
            start = 0 if i == 0 else (_text.sh_offset + sjs[i-1].address + len(sjs[i-1]))
            end = _text.sh_offset + sjs[i].address
            insn = new_insn(*ups[i])
            pieces.append(binary[start:end] + insn)
        pieces.append(binary[_text.sh_offset+sjs[-1].address+len(sjs[-1]):])
        self.__init__(''.join(pieces))
