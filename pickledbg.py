#!/usr/bin/env python3

#######################################################################################
# 
# pickledbg - a simple Python pickle debugger
# 
# The current pickle source code (https://github.com/python/cpython/blob/3.11/Lib/pickle.py) 
# was used as the framework for this debugger, only adding in code and making minor 
# modifications to print out the state and handle commands. The implementation of each 
# opcode was untouched to ensure it behaves exactly as it would when normally being 
# unpickled. The format and designed was meant to look and function similar to GEF, 
# including color schemes.

# Since pickles may be unloaded in a custom environment (extra modules imported, 
# builtins modified/removed, etc.), a short section exists in the source code to add in 
# that custom code. This should help simulate the unpickling process in an environment 
# as close to the real-world one as possible. In addition, some builtins may be removed 
# or overwritten either in the custom code or during the unpickling process, so 
# "copies" of several builtins (such as `print`, `input`, `bool`, etc.) were created, 
# and those copies are used inside any custom code added to the pickle source code. 
# This ensures that debugging functionality will still work properly even if the 
# environment is changed.
# 
#######################################################################################



### SAFE FUNCTIONS ###
# normal builtins are renamed so if overwritten during the pickle unload, they can still be accessed
from os import system as safe_system
from os import get_terminal_size as safe_get_terminal_size
safe_print = print
safe_input = input
safe_range = range
safe_max = max
safe_import = __import__
safe_bool = bool
safe_ascii = ascii
safe_type = type
safe_len = len
safe_open = open



############# INSERT CUSTOM CODE HERE #############



################# END CUSTOM CODE #################


### IMPORTS ###
import sys, io, codecs, tempfile, secrets
from struct import unpack
from copyreg import _inverted_registry, _extension_cache
import _compat_pickle, pickletools
from types import *
from opcodes import *
from colors import *
from unframer import _Unframer
from errors import UnpicklingError, PickleError, PicklingError, _Stop
from utils import _getattribute, whichmodule, encode_long, decode_long

### GLOBALS ###
pickle_bytes = b''
bytes_types = (bytes, bytearray)
HIGHEST_PROTOCOL = 5
pickle_disasm = []
disasm_line_no = 0

class _Unpickler:
    def __init__(self, file, *, fix_imports=True,
                 encoding="ASCII", errors="strict", buffers=None):
        """This takes a binary file for reading a pickle data stream.

        The protocol version of the pickle is detected automatically, so
        no proto argument is needed.

        The argument *file* must have two methods, a read() method that
        takes an integer argument, and a readline() method that requires
        no arguments.  Both methods should return bytes.  Thus *file*
        can be a binary file object opened for reading, an io.BytesIO
        object, or any other custom object that meets this interface.

        The file-like object must have two methods, a read() method
        that takes an integer argument, and a readline() method that
        requires no arguments.  Both methods should return bytes.
        Thus file-like object can be a binary file object opened for
        reading, a BytesIO object, or any other custom object that
        meets this interface.

        If *buffers* is not None, it should be an iterable of buffer-enabled
        objects that is consumed each time the pickle stream references
        an out-of-band buffer view.  Such buffers have been given in order
        to the *buffer_callback* of a Pickler object.

        If *buffers* is None (the default), then the buffers are taken
        from the pickle stream, assuming they are serialized there.
        It is an error for *buffers* to be None if the pickle stream
        was produced with a non-None *buffer_callback*.

        Other optional arguments are *fix_imports*, *encoding* and
        *errors*, which are used to control compatibility support for
        pickle stream generated by Python 2.  If *fix_imports* is True,
        pickle will try to map the old Python 2 names to the new names
        used in Python 3.  The *encoding* and *errors* tell pickle how
        to decode 8-bit string instances pickled by Python 2; these
        default to 'ASCII' and 'strict', respectively. *encoding* can be
        'bytes' to read these 8-bit string instances as bytes objects.
        """
        self.__file = file
        self._buffers = iter(buffers) if buffers is not None else None
        self._file_readline = file.readline
        self._file_read = file.read
        self.encoding = encoding
        self.errors = errors
        self.proto = 0
        self.fix_imports = fix_imports
        self.breakpoints = {}
        self.skip_next_breakpoint = False
        self.commands = [
            {
                "description": "Step to the next instruction",
                "list_of_aliases": ["step", "s"], 
                "handler": self.handle_step
            },
            {
                "description": "Run the pickle code",
                "list_of_aliases": ["run", "r", "start"],
                "handler": self.handle_run
            },
            {
                "description": "Exit the debugger",
                "list_of_aliases": ["exit", "quit", "q"],
                "handler": self.handle_exit
            },
            {
                "description": "Show this help menu",
                "list_of_aliases": ["help", "?"],
                "handler": self.handle_help
            },
            {
                "description": "Continue executing until the next breakpoint",
                "list_of_aliases": ["continue", "c"],
                "handler": self.handle_continue,
                "syntax": "continue [number_of_skipped_breakpoints]"
            },
            {
                "description": "Set a breakpoint at the specified line number or a specified number",
                "list_of_aliases": ["breakpoint", "break", "b"],
                "handler": self.handle_breakpoint,
                "syntax": "breakpoint [line_number] [function_name]"
            }
        ]

        self.load()

    def load(self):
        """Read a pickled object representation from the open file.

        Return the reconstituted object hierarchy specified in the file.
        """
        # Check whether Unpickler was initialized correctly. This is
        # only needed to mimic the behavior of _pickle.Unpickler.dump().
        if not hasattr(self, "_file_read"):
            raise UnpicklingError("Unpickler.__init__() was not called by "
                                  "%s.__init__()" % (self.__class__.__name__,))
        self._unframer = _Unframer(self._file_read, self._file_readline)
        self.read = self._unframer.read
        self.readinto = self._unframer.readinto
        self.readline = self._unframer.readline
        self.metastack = []
        self.stack = []
        self.memo = {}
        self.append = self.stack.append
        self.calling_function = []
        self.proto = 0

        self.last_command = None
        self.start = False

    def run(self):
        try:
            while True:
                self.handle_input()
        except _Stop as stopinst:
            return stopinst.value
    
    def check_for_breakpoint(self, key=b'i', obj=None):
        print(obj)
        for i,e in enumerate(([obj] if obj else []) or (self.stack + self.metastack)):
            print(e, key[0], hasattr(e, "__name__") or hasattr(e, "__qualname__"), self.skip_next_breakpoint)
            if hasattr(e, "__name__") or hasattr(e, "__qualname__"):
                if key[0] in (ord('R'), ord('i')):
                    funcname = e.__name__ if hasattr(e, "__name__") else e.__qualname__
                    arguments = [self.stack[i]] if key[0] == ord('i') else self.stack[i + 1]

                    if funcname in self.breakpoints:
                        print("HITTINg")
                        if not self.skip_next_breakpoint:
                            self.__file.seek(self.__file.tell() - 1)
                            self.calling_function = (funcname, e.__module__, arguments) if hasattr(e, "__module__") else (*e.__qualname__.split("."), arguments)

                            raise EOFError
                        self.skip_next_breakpoint = False

    def next_instruction(self, single=False):
        global pickle_disasm
        global disasm_line_no

        key = self.read(1)
        
        if not key:
            raise EOFError
        assert isinstance(key, bytes_types)

        try:
            self.check_for_breakpoint(key)
            self.dispatch[key[0]](self)
            disasm_line_no += 1
            return True
        except EOFError:
            self.print_entire_state()
            return False

    def handle_step(self, _):
        if not self.start:
            safe_print(redify("[!] Must run the debugger first."))
            return
    
        self.print_entire_state() if not self.next_instruction(single=True) else self.print_state()


    def handle_breakpoint(self, inp):
        function_name = inp[0]
        self.breakpoints[function_name] = 0

    def handle_exit(self, _):
        raise _Stop(None)

    def handle_run(self, _):
        global pickle_disasm
        global disasm_line_no

        self.load()
        self.__file.seek(0)

        disasm_line_no = 0
        self.start = True

        while self.next_instruction(): ...

    def handle_continue(self, inp):pass

    def print_entire_state(self):
        self.skip_next_breakpoint = True
        self.print_state()
        if self.calling_function:
            terminal_width = safe_get_terminal_size()[0]
            safe_print(grayify(''.join(['─' for _ in safe_range(terminal_width-16)]))+cyanify(' arguments ')+grayify('────'))
            safe_print(f"{blueify(self.calling_function[0])}@{self.calling_function[1]} (")
            
            for arg in self.calling_function[2]:
                to_call = {
                    "dict": colorize_dict,
                    "list": colorize_array,
                    "tuple": colorize_array,
                    "str": lambda x: pinkify(ascii(x)), 
                }[safe_type(arg).__name__]
                safe_print(f"    {to_call(arg)},")
            safe_print(")")

            self.calling_function = []

    def handle_help(self, _):
        terminal_width = safe_get_terminal_size()[0]
        lengths = (terminal_width - len(' pickledbg help'))//2
        safe_print(grayify('─'*lengths)+cyanify(' pickledbg help ')+grayify('─'*lengths))

        for cmd in self.commands:
            safe_print(cyanify("start"))
            safe_print(redify(cmd["description"]))
            if "list_of_aliases" in cmd:
                safe_print(yellowify("Aliases:")+f' {", ".join(cmd["list_of_aliases"])}')
            if "syntax" in cmd:
                safe_print(yellowify("Syntax:")+f' {cmd["syntax"]}')
            safe_print()
            safe_print(grayify('─'*terminal_width))

    def handle_input(self, inp=None):
        global pickle_disasm
        global disasm_line_no

        if inp is None:
            safe_print((greenify if self.start else redify)("pickledbg>  "), end="")
            try:
                inp = safe_input()
            except (EOFError, KeyboardInterrupt):
                safe_print(redify("\n[+] Exiting..."))
                raise _Stop(None)
            
        inp = inp or self.last_command
        parts = inp.split()
        for cmd in self.commands:
            match parts[0]:
                case x if x in cmd["list_of_aliases"]:
                    cmd["handler"](parts[1:])
                    break
        else:
            safe_print(redify("[!] Invalid command. Type 'help' for a list of available commands."))

        self.last_command = inp

        # elif inp == "help" or inp == "?":
        #     self.last_command = inp

        #     terminal_width = safe_get_terminal_size()[0]
        #     lengths = (terminal_width - len(' pickledbg help'))//2
        #     safe_print(grayify('─'*lengths)+cyanify(' pickledbg help ')+grayify('─'*lengths))

        #     # start
        #     safe_print(redify("start"))
        #     safe_print("Starts the debugger, pointing to the first instruction but not executing it. Must only be ran once. To restart debugging, close the program and run it again. Must also be run before stepping through instructions.")
        #     safe_print(yellowify("Aliases:")+' run')
        #     safe_print()
        #     safe_print(grayify('─'*terminal_width))

        #     # ni
        #     safe_print(redify("ni"))
        #     safe_print("Executes the next instruction and shows the updated Pickle Machine state. Must be ran after 'start'.")
        #     safe_print(yellowify("Aliases:")+' next')
        #     safe_print()
        #     safe_print(grayify('─'*terminal_width))

        #     # export 
        #     safe_print(redify("export"))
        #     safe_print("Writes the disassembly of the pickle to a file. If no filename is specified, the default is 'out.disasm'.")
        #     safe_print(yellowify("Syntax:")+' export [filename]')
        #     safe_print()
        #     safe_print(grayify('─'*terminal_width))

        #     # help
        #     safe_print(redify("help"))
        #     safe_print("Shows this help menu.")
        #     safe_print(yellowify("Aliases:")+' ?')
        #     safe_print()
        #     safe_print(grayify('─'*terminal_width))

        #     # exit
        #     safe_print(redify("exit"))
        #     safe_print("Exits the debugger.")
        #     safe_print(yellowify("Aliases:")+' quit')
        #     safe_print()
        #     safe_print(grayify('─'*terminal_width))

    def print_state(self):
        safe_system('clear -x')

        ### STACK & MEMO ###
        terminal_width = safe_get_terminal_size()[0]
        safe_print(grayify(''.join(['─' for _ in safe_range(terminal_width-17)]))+cyanify(' stack & memo ')+grayify('───'))
        safe_print(blueify("stack     ")+": ", colorize_array(self.stack))
        if self.metastack != []: safe_print(blueify("metastack ")+": ", colorize_array(self.metastack))
        safe_print(blueify("memo      ")+": ", colorize_dict(self.memo))

        ### DISASSEMBLY ###
        safe_print(grayify(''.join(['─' for _ in safe_range(terminal_width-16)]))+cyanify(' disassembly ')+grayify('───'))
        
        tmp = '\n   '.join(pickle_disasm[safe_max(0,disasm_line_no-3):disasm_line_no])
        if tmp != '':
            safe_print('   '+grayify(tmp))

        safe_print(greenify('-> '+pickle_disasm[disasm_line_no]))
        tmp = '\n   '.join(pickle_disasm[disasm_line_no+1:disasm_line_no+4])
        if tmp != '':
            safe_print('   '+tmp)
            
        # safe_print(grayify(''.join(['─' for _ in safe_range(terminal_width)])))

    # Return a list of items pushed in the stack after last MARK instruction.
    def pop_mark(self):
        items = self.stack
        self.stack = self.metastack.pop()
        self.append = self.stack.append
        return items

    def persistent_load(self, pid):
        raise UnpicklingError("unsupported persistent id encountered")

    dispatch = {}

    def load_proto(self):
        proto = self.read(1)[0]
        if not 0 <= proto <= HIGHEST_PROTOCOL:
            raise ValueError("unsupported pickle protocol: %d" % proto)
        self.proto = proto
    dispatch[PROTO[0]] = load_proto

    def load_frame(self):
        frame_size, = unpack('<Q', self.read(8))
        if frame_size > sys.maxsize:
            raise ValueError("frame size > sys.maxsize: %d" % frame_size)
        self._unframer.load_frame(frame_size)
    dispatch[FRAME[0]] = load_frame

    def load_persid(self):
        try:
            pid = self.readline()[:-1].decode("ascii")
        except UnicodeDecodeError:
            raise UnpicklingError(
                "persistent IDs in protocol 0 must be ASCII strings")
        self.append(self.persistent_load(pid))
    dispatch[PERSID[0]] = load_persid

    def load_binpersid(self):
        pid = self.stack.pop()
        self.append(self.persistent_load(pid))
    dispatch[BINPERSID[0]] = load_binpersid

    def load_none(self):
        self.append(None)
    dispatch[NONE[0]] = load_none

    def load_false(self):
        self.append(False)
    dispatch[NEWFALSE[0]] = load_false

    def load_true(self):
        self.append(True)
    dispatch[NEWTRUE[0]] = load_true

    def load_int(self):
        data = self.readline()
        if data == FALSE[1:]:
            val = False
        elif data == TRUE[1:]:
            val = True
        else:
            val = int(data, 0)
        self.append(val)
    dispatch[INT[0]] = load_int

    def load_binint(self):
        self.append(unpack('<i', self.read(4))[0])
    dispatch[BININT[0]] = load_binint

    def load_binint1(self):
        self.append(self.read(1)[0])
    dispatch[BININT1[0]] = load_binint1

    def load_binint2(self):
        self.append(unpack('<H', self.read(2))[0])
    dispatch[BININT2[0]] = load_binint2

    def load_long(self):
        val = self.readline()[:-1]
        if val and val[-1] == b'L'[0]:
            val = val[:-1]
        self.append(int(val, 0))
    dispatch[LONG[0]] = load_long

    def load_long1(self):
        n = self.read(1)[0]
        data = self.read(n)
        self.append(decode_long(data))
    dispatch[LONG1[0]] = load_long1

    def load_long4(self):
        n, = unpack('<i', self.read(4))
        if n < 0:
            # Corrupt or hostile pickle -- we never write one like this
            raise UnpicklingError("LONG pickle has negative byte count")
        data = self.read(n)
        self.append(decode_long(data))
    dispatch[LONG4[0]] = load_long4

    def load_float(self):
        self.append(float(self.readline()[:-1]))
    dispatch[FLOAT[0]] = load_float

    def load_binfloat(self):
        self.append(unpack('>d', self.read(8))[0])
    dispatch[BINFLOAT[0]] = load_binfloat

    def _decode_string(self, value):
        # Used to allow strings from Python 2 to be decoded either as
        # bytes or Unicode strings.  This should be used only with the
        # STRING, BINSTRING and SHORT_BINSTRING opcodes.
        if self.encoding == "bytes":
            return value
        else:
            return value.decode(self.encoding, self.errors)

    def load_string(self):
        data = self.readline()[:-1]
        # Strip outermost quotes
        if len(data) >= 2 and data[0] == data[-1] and data[0] in b'"\'':
            data = data[1:-1]
        else:
            raise UnpicklingError("the STRING opcode argument must be quoted")
        self.append(self._decode_string(codecs.escape_decode(data)[0]))
    dispatch[STRING[0]] = load_string

    def load_binstring(self):
        # Deprecated BINSTRING uses signed 32-bit length
        len, = unpack('<i', self.read(4))
        if len < 0:
            raise UnpicklingError("BINSTRING pickle has negative byte count")
        data = self.read(len)
        self.append(self._decode_string(data))
    dispatch[BINSTRING[0]] = load_binstring

    def load_binbytes(self):
        len, = unpack('<I', self.read(4))
        if len > sys.maxsize:
            raise UnpicklingError("BINBYTES exceeds system's maximum size "
                                  "of %d bytes" % sys.maxsize)
        self.append(self.read(len))
    dispatch[BINBYTES[0]] = load_binbytes

    def load_unicode(self):
        self.append(str(self.readline()[:-1], 'raw-unicode-escape'))
    dispatch[UNICODE[0]] = load_unicode

    def load_binunicode(self):
        len, = unpack('<I', self.read(4))
        if len > sys.maxsize:
            raise UnpicklingError("BINUNICODE exceeds system's maximum size "
                                  "of %d bytes" % sys.maxsize)
        self.append(str(self.read(len), 'utf-8', 'surrogatepass'))
    dispatch[BINUNICODE[0]] = load_binunicode

    def load_binunicode8(self):
        len, = unpack('<Q', self.read(8))
        if len > sys.maxsize:
            raise UnpicklingError("BINUNICODE8 exceeds system's maximum size "
                                  "of %d bytes" % sys.maxsize)
        self.append(str(self.read(len), 'utf-8', 'surrogatepass'))
    dispatch[BINUNICODE8[0]] = load_binunicode8

    def load_binbytes8(self):
        len, = unpack('<Q', self.read(8))
        if len > sys.maxsize:
            raise UnpicklingError("BINBYTES8 exceeds system's maximum size "
                                  "of %d bytes" % sys.maxsize)
        self.append(self.read(len))
    dispatch[BINBYTES8[0]] = load_binbytes8

    def load_bytearray8(self):
        len, = unpack('<Q', self.read(8))
        if len > sys.maxsize:
            raise UnpicklingError("BYTEARRAY8 exceeds system's maximum size "
                                  "of %d bytes" % sys.maxsize)
        b = bytearray(len)
        self.readinto(b)
        self.append(b)
    dispatch[BYTEARRAY8[0]] = load_bytearray8

    def load_next_buffer(self):
        if self._buffers is None:
            raise UnpicklingError("pickle stream refers to out-of-band data "
                                  "but no *buffers* argument was given")
        try:
            buf = next(self._buffers)
        except StopIteration:
            raise UnpicklingError("not enough out-of-band buffers")
        self.append(buf)
    dispatch[NEXT_BUFFER[0]] = load_next_buffer

    def load_readonly_buffer(self):
        buf = self.stack[-1]
        with memoryview(buf) as m:
            if not m.readonly:
                self.stack[-1] = m.toreadonly()
    dispatch[READONLY_BUFFER[0]] = load_readonly_buffer

    def load_short_binstring(self):
        len = self.read(1)[0]
        data = self.read(len)
        self.append(self._decode_string(data))
    dispatch[SHORT_BINSTRING[0]] = load_short_binstring

    def load_short_binbytes(self):
        len = self.read(1)[0]
        self.append(self.read(len))
    dispatch[SHORT_BINBYTES[0]] = load_short_binbytes

    def load_short_binunicode(self):
        len = self.read(1)[0]
        self.append(str(self.read(len), 'utf-8', 'surrogatepass'))
    dispatch[SHORT_BINUNICODE[0]] = load_short_binunicode

    def load_tuple(self):
        items = self.pop_mark()
        self.append(tuple(items))
    dispatch[TUPLE[0]] = load_tuple

    def load_empty_tuple(self):
        self.append(())
    dispatch[EMPTY_TUPLE[0]] = load_empty_tuple

    def load_tuple1(self):
        self.stack[-1] = (self.stack[-1],)
    dispatch[TUPLE1[0]] = load_tuple1

    def load_tuple2(self):
        self.stack[-2:] = [(self.stack[-2], self.stack[-1])]
    dispatch[TUPLE2[0]] = load_tuple2

    def load_tuple3(self):
        self.stack[-3:] = [(self.stack[-3], self.stack[-2], self.stack[-1])]
    dispatch[TUPLE3[0]] = load_tuple3

    def load_empty_list(self):
        self.append([])
    dispatch[EMPTY_LIST[0]] = load_empty_list

    def load_empty_dictionary(self):
        self.append({})
    dispatch[EMPTY_DICT[0]] = load_empty_dictionary

    def load_empty_set(self):
        self.append(set())
    dispatch[EMPTY_SET[0]] = load_empty_set

    def load_frozenset(self):
        items = self.pop_mark()
        self.append(frozenset(items))
    dispatch[FROZENSET[0]] = load_frozenset

    def load_list(self):
        items = self.pop_mark()
        self.append(items)
    dispatch[LIST[0]] = load_list

    def load_dict(self):
        items = self.pop_mark()
        d = {items[i]: items[i+1]
             for i in range(0, len(items), 2)}
        self.append(d)
    dispatch[DICT[0]] = load_dict

    # INST and OBJ differ only in how they get a class object.  It's not
    # only sensible to do the rest in a common routine, the two routines
    # previously diverged and grew different bugs.
    # klass is the class to instantiate, and k points to the topmost mark
    # object, following which are the arguments for klass.__init__.
    def _instantiate(self, klass, args):
        if (args or not isinstance(klass, type) or
            hasattr(klass, "__getinitargs__")):
            try:
                value = klass(*args)
            except TypeError as err:
                raise TypeError("in constructor for %s: %s" %
                                (klass.__name__, str(err)), sys.exc_info()[2])
        else:
            value = klass.__new__(klass)
        self.append(value)

    def load_inst(self):
        module = self.readline()[:-1].decode("ascii")
        name = self.readline()[:-1].decode("ascii")

        klass = self.find_class(module, name)
        
        self.check_for_breakpoint(obj=klass)
        self._instantiate(klass, self.pop_mark())
    dispatch[INST[0]] = load_inst

    def load_obj(self):
        # Stack is ... markobject classobject arg1 arg2 ...
        args = self.pop_mark()
        cls = args.pop(0)
        self._instantiate(cls, args)
    dispatch[OBJ[0]] = load_obj

    def load_newobj(self):
        args = self.stack.pop()
        cls = self.stack.pop()
        obj = cls.__new__(cls, *args)
        self.append(obj)
    dispatch[NEWOBJ[0]] = load_newobj

    def load_newobj_ex(self):
        kwargs = self.stack.pop()
        args = self.stack.pop()
        cls = self.stack.pop()
        obj = cls.__new__(cls, *args, **kwargs)
        self.append(obj)
    dispatch[NEWOBJ_EX[0]] = load_newobj_ex

    def load_global(self):
        module = self.readline()[:-1].decode("utf-8")
        name = self.readline()[:-1].decode("utf-8")
        klass = self.find_class(module, name)
        self.append(klass)
    dispatch[GLOBAL[0]] = load_global

    def load_stack_global(self):
        name = self.stack.pop()
        module = self.stack.pop()
        if type(name) is not str or type(module) is not str:
            raise UnpicklingError("STACK_GLOBAL requires str")
        self.append(self.find_class(module, name))
    dispatch[STACK_GLOBAL[0]] = load_stack_global

    def load_ext1(self):
        code = self.read(1)[0]
        self.get_extension(code)
    dispatch[EXT1[0]] = load_ext1

    def load_ext2(self):
        code, = unpack('<H', self.read(2))
        self.get_extension(code)
    dispatch[EXT2[0]] = load_ext2

    def load_ext4(self):
        code, = unpack('<i', self.read(4))
        self.get_extension(code)
    dispatch[EXT4[0]] = load_ext4

    def get_extension(self, code):
        nil = []
        obj = _extension_cache.get(code, nil)
        if obj is not nil:
            self.append(obj)
            return
        key = _inverted_registry.get(code)
        if not key:
            if code <= 0: # note that 0 is forbidden
                # Corrupt or hostile pickle.
                raise UnpicklingError("EXT specifies code <= 0")
            raise ValueError("unregistered extension code %d" % code)
        obj = self.find_class(*key)
        _extension_cache[code] = obj
        self.append(obj)

    def find_class(self, module, name):
        # Subclasses may override this.
        sys.audit('pickle.find_class', module, name)
        if self.proto < 3 and self.fix_imports:
            if (module, name) in _compat_pickle.NAME_MAPPING:
                module, name = _compat_pickle.NAME_MAPPING[(module, name)]
            elif module in _compat_pickle.IMPORT_MAPPING:
                module = _compat_pickle.IMPORT_MAPPING[module]
        __import__(module, level=0)
        if self.proto >= 4:
            return _getattribute(sys.modules[module], name)[0]
        else:
            return getattr(sys.modules[module], name)

    def load_reduce(self):
        stack = self.stack
        args = stack.pop()
        func = stack[-1]
        stack[-1] = func(*args)
    dispatch[REDUCE[0]] = load_reduce

    def load_pop(self):
        if self.stack:
            del self.stack[-1]
        else:
            self.pop_mark()
    dispatch[POP[0]] = load_pop

    def load_pop_mark(self):
        self.pop_mark()
    dispatch[POP_MARK[0]] = load_pop_mark

    def load_dup(self):
        self.append(self.stack[-1])
    dispatch[DUP[0]] = load_dup

    def load_get(self):
        i = int(self.readline()[:-1])
        try:
            self.append(self.memo[i])
        except KeyError:
            msg = f'Memo value not found at index {i}'
            raise UnpicklingError(msg) from None
    dispatch[GET[0]] = load_get

    def load_binget(self):
        i = self.read(1)[0]
        try:
            self.append(self.memo[i])
        except KeyError as exc:
            msg = f'Memo value not found at index {i}'
            raise UnpicklingError(msg) from None
    dispatch[BINGET[0]] = load_binget

    def load_long_binget(self):
        i, = unpack('<I', self.read(4))
        try:
            self.append(self.memo[i])
        except KeyError as exc:
            msg = f'Memo value not found at index {i}'
            raise UnpicklingError(msg) from None
    dispatch[LONG_BINGET[0]] = load_long_binget

    def load_put(self):
        i = int(self.readline()[:-1])
        if i < 0:
            raise ValueError("negative PUT argument")
        self.memo[i] = self.stack[-1]
    dispatch[PUT[0]] = load_put

    def load_binput(self):
        i = self.read(1)[0]
        if i < 0:
            raise ValueError("negative BINPUT argument")
        self.memo[i] = self.stack[-1]
    dispatch[BINPUT[0]] = load_binput

    def load_long_binput(self):
        i, = unpack('<I', self.read(4))
        if i > sys.maxsize:
            raise ValueError("negative LONG_BINPUT argument")
        self.memo[i] = self.stack[-1]
    dispatch[LONG_BINPUT[0]] = load_long_binput

    def load_memoize(self):
        memo = self.memo
        memo[len(memo)] = self.stack[-1]
    dispatch[MEMOIZE[0]] = load_memoize

    def load_append(self):
        stack = self.stack
        value = stack.pop()
        list = stack[-1]
        list.append(value)
    dispatch[APPEND[0]] = load_append

    def load_appends(self):
        items = self.pop_mark()
        list_obj = self.stack[-1]
        try:
            extend = list_obj.extend
        except AttributeError:
            pass
        else:
            extend(items)
            return
        # Even if the PEP 307 requires extend() and append() methods,
        # fall back on append() if the object has no extend() method
        # for backward compatibility.
        append = list_obj.append
        for item in items:
            append(item)
    dispatch[APPENDS[0]] = load_appends

    def load_setitem(self):
        stack = self.stack
        value = stack.pop()
        key = stack.pop()
        dict = stack[-1]
        dict[key] = value
    dispatch[SETITEM[0]] = load_setitem

    def load_setitems(self):
        items = self.pop_mark()
        dict = self.stack[-1]
        for i in range(0, len(items), 2):
            dict[items[i]] = items[i + 1]
    dispatch[SETITEMS[0]] = load_setitems

    def load_additems(self):
        items = self.pop_mark()
        set_obj = self.stack[-1]
        if isinstance(set_obj, set):
            set_obj.update(items)
        else:
            add = set_obj.add
            for item in items:
                add(item)
    dispatch[ADDITEMS[0]] = load_additems

    def load_build(self):
        stack = self.stack
        state = stack.pop()
        inst = stack[-1]
        setstate = getattr(inst, "__setstate__", None)
        if setstate is not None:
            setstate(state)
            return
        slotstate = None
        if isinstance(state, tuple) and len(state) == 2:
            state, slotstate = state
        if state:
            inst_dict = inst.__dict__
            intern = sys.intern
            for k, v in state.items():
                if type(k) is str:
                    inst_dict[intern(k)] = v
                else:
                    inst_dict[k] = v
        if slotstate:
            for k, v in slotstate.items():
                setattr(inst, k, v)
    dispatch[BUILD[0]] = load_build

    def load_mark(self):
        self.metastack.append(self.stack)
        self.stack = []
        self.append = self.stack.append
    dispatch[MARK[0]] = load_mark

    def load_stop(self):
        value = self.stack.pop()
        raise _Stop(value)
    dispatch[STOP[0]] = load_stop



### MAIN ###
if __name__ == "__main__":
    # check that pickle file is provided
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <picklefile>")
        sys.exit(1)

    # try to read pickle_file
    try:
        pickle_file = open(sys.argv[1], "rb")
    except:
        print(redify("[!] Error: could not open pickle file"))
        sys.exit(1)

    # get pickletools disassembly
    try:
        tmpdir = tempfile.gettempdir()
        tmpname = tmpdir + '/tmp' + secrets.token_hex(6)
        with open(tmpname, "w") as tmpfile:
            pickletools.dis(pickle_file, out=tmpfile)
        pickle_disasm = open(tmpname, "r").read().split('\n')[:-2]
        __import__('os').remove(tmpname)
    except:
        print(redify("[!] Error: could not disassemble pickle file"))
        sys.exit(1)
    
    _Unpickler(open(sys.argv[1], "rb")).run()