# Volatility
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details. 
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA 

"""
@author:       Andrew Case
@license:      GNU General Public License 2.0 or later
@contact:      atcuno@gmail.com
@organization: Digital Forensics Solutions
"""

import volatility.commands as commands
import volatility.utils as utils
import volatility.debug as debug
import volatility.obj as obj
import volatility.plugins.linux.flags as linux_flags

MAX_STRING_LENGTH = 256

import time
nsecs_per = 1000000000

def mask_number(num):
    return num & 0xffffffff


class AbstractLinuxCommand(commands.Command):

    def __init__(self, *args, **kwargs):
        commands.Command.__init__(self, *args, **kwargs)
        self.addr_space = utils.load_as(self._config)
        self.profile = self.addr_space.profile
        self.smap = self.profile.sysmap

    @staticmethod
    def is_valid_profile(profile):
        return profile.metadata.get('os', 'Unknown').lower() == 'linux'

# returns a list of online cpus (the processor numbers)
def online_cpus(smap, addr_space):

    #later kernels..
    if "cpu_online_bits" in smap:
        bmap = obj.Object("unsigned long", offset = smap["cpu_online_bits"], vm = addr_space)

    elif "cpu_present_map" in smap:
        bmap = obj.Object("unsigned long", offset = smap["cpu_present_map"], vm = addr_space)

    else:
        raise AttributeError, "Unable to determine number of online CPUs for memory capture"

    cpus = []
    for i in range(8):
        if bmap & (1 << i):
            cpus.append(i)

    return cpus

def walk_per_cpu_var(obj_ref, per_var, var_type):

    cpus = online_cpus(obj_ref.smap, obj_ref.addr_space)

    # get the highest numbered cpu
    max_cpu = cpus[-1] + 1

    offset_var = "__per_cpu_offset"
    per_offsets = obj.Object(theType = 'Array', targetType = 'unsigned long', count = max_cpu, offset = obj_ref.smap[offset_var], vm = obj_ref.addr_space)

    i = 0

    for i in range(max_cpu):

        offset = per_offsets[i]
       
        if "per_cpu__" + per_var in obj_ref.smap:
            var = "per_cpu__" + per_var
        else:
            var = per_var

        addr = obj_ref.smap[var] + offset.v()
        var = obj.Object(var_type, offset = addr, vm = obj_ref.addr_space)

        yield i, var

# based on 2.6.35 getboottime
def get_boot_time(self):

    wall_addr  = self.smap["wall_to_monotonic"]
    wall       = obj.Object("timespec", offset=wall_addr, vm=self.addr_space)

    sleep_addr = self.smap["total_sleep_time"]
    timeo      = obj.Object("timespec", offset=sleep_addr, vm=self.addr_space)

    secs  = wall.tv_sec  + timeo.tv_sec
    nsecs = wall.tv_nsec + timeo.tv_nsec 

    secs  = secs  * -1
    nsecs = nsecs * -1

    while nsecs >= nsecs_per:

        nsecs = nsecs - nsecs_per

        secs = secs + 1

    while nsecs < 0:

        nsecs = nsecs + nsecs_per

        secs = secs - 1

    boot_time = secs + (nsecs / nsecs_per / 100)

    return boot_time


# similar to for_each_process for this usage
def walk_list_head(struct_name, list_member, list_head_ptr, _addr_space):
    debug.warning("Deprecated use of walk_list_head")

    for item in list_head_ptr.list_of_type(struct_name, list_member):
        yield item


def walk_internal_list(struct_name, list_member, list_start, addr_space = None):
    if not addr_space:
        addr_space = list_start.obj_vm

    while list_start:
        list_struct = obj.Object(struct_name, vm = addr_space, offset = list_start.v())
        yield list_struct
        list_start = getattr(list_struct, list_member)


# based on __d_path
# TODO: (deleted) support
def do_get_path(rdentry, rmnt, dentry, vfsmnt):
    ret_path = []

    inode = dentry.d_inode

    if not rdentry.is_valid() or not dentry.is_valid():
        return []

    while dentry != rdentry or vfsmnt != rmnt:
        
        dname = dentry.d_name.name.dereference_as("String", length = MAX_STRING_LENGTH)
        
        ret_path.append(dname.strip('/'))
        
        if dentry == vfsmnt.mnt_root or dentry == dentry.d_parent:
            if vfsmnt.mnt_parent == vfsmnt.v():
                break
            dentry = vfsmnt.mnt_mountpoint
            vfsmnt = vfsmnt.mnt_parent
            continue

        parent = dentry.d_parent
        dentry = parent

    ret_path.reverse()

    ret_val = '/'.join([str(p) for p in ret_path if p != ""])

    if ret_val.startswith(("socket:", "pipe:")):
        if ret_val.find("]") == -1:
            ret_val = ret_val[:-1] + ":[{0}]".format(inode.i_ino)
        else:
            ret_val = ret_val.replace("/", "")

    elif ret_val != "inotify":
        ret_val = '/' + ret_val

    return ret_val


def get_path(task, filp):
    rdentry = task.fs.get_root_dentry()
    rmnt = task.fs.get_root_mnt()
    dentry = filp.dentry
    vfsmnt = filp.vfsmnt

    return do_get_path(rdentry, rmnt, dentry, vfsmnt)

def get_obj(self, ptr, sname, member):

    offset = self.profile.get_obj_offset(sname, member)

    addr  = ptr - offset

    return obj.Object(sname, offset = addr, vm = self.addr_space)

def S_ISDIR(mode):
    return (mode & linux_flags.S_IFMT) == linux_flags.S_IFDIR

def S_ISREG(mode):
    return (mode & linux_flags.S_IFMT) == linux_flags.S_IFREG


