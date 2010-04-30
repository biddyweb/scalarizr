'''
Created on Apr 1, 2010

@author: marat
'''
from scalarizr.bus import bus
from ConfigParser import RawConfigParser
import logging
import binascii
import os


class ConfigError(BaseException):
	pass

RET_BOTH = 1
RET_PUBLIC = 2
RET_PRIVATE = 3

#TODO: better to move these constants into 'scalarizr' package 
SECT_GENERAL = "general"
OPT_SERVER_ID = "server_id"
OPT_BEHAVIOUR = "behaviour"
OPT_STORAGE_PATH = "storage_path"
OPT_CRYPTO_KEY_PATH = "crypto_key_path"
OPT_PLATFORM = "platform"
OPT_QUERYENV_URL = "queryenv_url"
OPT_SCRIPTS_PATH = "scripts_path"

SECT_MESSAGING = "messaging"
OPT_ADAPTER = "adapter"

SECT_HANDLERS = "handlers"

def _get_filename(basename, ret):
	if ret == RET_PUBLIC:
		return os.path.join(get_public_d_path(), basename)
	elif ret == RET_PRIVATE:
		return os.path.join(get_private_d_path(), basename)
	elif ret == RET_BOTH:
		return (os.path.join(get_public_d_path(), basename), 
			os.path.join(get_private_d_path(), basename))
	else:
		raise ConfigError("Incorrect method call.`ret` must be one of RET_* constants")

def get_handler_filename(handler_name, ret=RET_BOTH):
	return _get_filename("handler.%s.ini" % (handler_name), ret)

def get_behaviour_filename(behaviour_name, ret=RET_BOTH):
	return _get_filename("behaviour.%s.ini" % (behaviour_name), ret)	

def get_platform_filename(platform_name, ret=RET_BOTH):
	return _get_filename("platform.%s.ini" % (platform_name), ret)

def get_platform_section_name(platform_name):
	return "platform_%s" % (platform_name)

def get_behaviour_section_name(behaviour_name):
	return "behaviour_%s" % (behaviour_name)

def get_handler_section_name(handler_name):
	return "handler_%s" % (handler_name)
	
def get_public_d_path(basename=None):
	args = [bus.etc_path, "public.d"]
	if basename:
		args.append(basename)
	return os.path.join(*args)

def get_private_d_path(basename=None):
	args = [bus.etc_path, "private.d"]
	if basename:
		args.append(basename)
	return os.path.join(*args)

def get_d_path(private, basename=None):
	return get_private_d_path(basename) if private else get_public_d_path(basename)
	
def get_key_filename(key_name, private=True):
	return os.path.join(get_d_path(private), "keys", key_name)
	
def write_key(path, key, key_title=None, private=None, base64encode=False):
	"""
	Writes key into $etc/.private.d/keys, $etc/public.d/keys
	"""
	filename = os.path.join(bus.etc_path, path) if private is None \
			else get_key_filename(os.path.basename(path), private)
	file = None
	try:
		os.chmod(filename, 0600)
		file = open(filename, "w+")
		file.write(binascii.b2a_base64(key) if base64encode else key)
		os.chmod(filename, 0400)
	except OSError, e:
		raise ConfigError("Cannot write %s in file '%s'. %s" % (key_title or "key", filename, str(e)))
	finally:
		if file:
			file.close()
	
def read_key(path, key_title=None, private=None):
	"""
	Reads key from $etc/.private.d/keys, $etc/public.d/keys
	"""
	filename = os.path.join(bus.etc_path, path) if private is None \
			else get_key_filename(os.path.basename(path), private)
	file = None
	try:
		file = open(filename, "r")
		return file.read().strip()
	except OSError, e:
		raise ConfigError("Cannot read %s file '%s'. %s" % (key_title or "key", filename, str(e)))
	finally:
		if file:
			file.close()

def split_array(value, separator=",", allow_empty=False, ct=list):
	return ct(v.strip() for v in value.split(separator) if allow_empty or (not allow_empty and v))

def update(filename, sections):
	class Comment:
		type = "comment"
		def __init__(self, text):
			self.text = text
		def __str__(self):
			return self.text
		
	class Option:
		type = "option"
		def __init__(self, key, value):
			self.key = key
			self.value = value
		def __str__(self):
			return "%s = %s%s" % (self.key, self.value, os.linesep)
	
	class Section:
		type = "section"
		def __init__(self, name):
			self.items = []
			self.name = name
		def __str__(self):
			ret = "[%s]%s" % (self.name, os.linesep)
			for item in self.items:
				ret += str(item)
			return ret
		
	class Config:
		def __init__(self):
			self.items = []
		def __str__(self):
			ret = ""
			for item in self.items:
				ret += str(item)
			return ret
		
	logger = logging.getLogger(__name__)
		
	# Read configuration from file
	config = Config()
	if os.path.exists(filename):
		cursect = None
		sect_re = RawConfigParser.SECTCRE
		opt_re = RawConfigParser.OPTCRE
		fp = open(filename, "r")
		while True:
			line = fp.readline()
			if not line:
				break
			mo = sect_re.match(line)
			if mo:
				cursect = Section(mo.group('header').strip())
				config.items.append(cursect)
			else:
				mo = opt_re.match(line)
				if mo:
					cursect.items.append(Option(mo.group("option").strip(), mo.group("value").strip()))
				else:
					comment = Comment(line)
					if cursect:
						cursect.items.append(comment)
					else:
						config.items.append(comment)
		fp.close()
		fp = None
	
	
	logger.debug("Update configuration...")
	
	# Update configuration
	for sect_name in sections:
		logger.debug("Find section '%s' in existing sections", sect_name)
		cur_sect = None
		for section in [it for it in config.items if it.type == "section"]:
			logger.debug("Compare '%s' with '%s'", sect_name, section.name)
			if section.name == sect_name:
				logger.debug("Found '%s' in existing sections", sect_name)
				cur_sect = section
				break
		# Section not found
		if cur_sect is None:
			# Create new section and append it in the end
			logger.debug("Section '%s' wasn't found in existing sections", sect_name)
			logger.debug("Create section '%s' and append it in the end", sect_name)
			cur_sect = Section(sect_name)
			config.items.append(cur_sect)
			
		for opt_name, value in sections[sect_name].items():
			logger.debug("Find option '%s' in section '%s'", opt_name, sect_name)
			cur_opt = None
			for option in [it for it in cur_sect.items if it.type == "option"]:
				logger.debug("Compare '%s' with '%s'", opt_name, option.key)
				if option.key == opt_name:
					logger.debug("Found option '%s' in existing options in section '%s'", 
							opt_name, sect_name)
					cur_opt = option
					break
			# Option not found
			if cur_opt is None:
				logger.debug("Option '%s' wasn't found in existing options of section '%s'", 
						opt_name, sect_name)
				logger.debug("Create option '%s' and append it in the end of section '%s'", 
						opt_name, sect_name)
				# Create option and append it in the end
				cur_opt = Option(opt_name, value)
				cur_sect.items.append(cur_opt)
			else:
				cur_opt.value = value
	

	logger.debug("Write result configuration into file '%s'", filename)
	try:
		fp = open(filename, "w")
		fp.write(str(config))
	finally:
		if fp:
			fp.close()
			
	
class _OptionWrapper(object):
	def __init__(self, *args):
		if isinstance(args[0], _SectionWrapper):
			self.config = args[0].config
			self.section = args[0].section
			self.option = args[1]
		elif len(args) == 3:
			self.config = args[0]
			self.section = args[1]
			self.option = args[2]
		else:
			raise AttributeError()
		
		self.name = "[%s]%s" % (self.section, self.option)
	
	def get(self): return self.config.get(self.section, self.option)
	
	def set(self, value): self.config.set(self.section, self.option, value)
			
	def remove(self, name): return self.config.remove_option(self.section, self.option)
	
	def set_required(self, value, ex_class=ConfigError):
		if value:
			self.set(value)
		if not self.get():
			raise ex_class("Configuration option %s is not defined" % (self.name))

			
def option_wrapper(*args):
	return _OptionWrapper(*args)
	
	
class _SectionWrapper(object):
	def __init__(self, config, section):
		self.config = config
		self.section = section
		self.name = self.section
		
	def set(self, option, value): self.config.set(self.section, option, value)
	
	def get(self, option): return self.config.get(self.section, option)
	
	def remove_option(self, option): return self.config.remove_option(self.section, option)
	
	def remove(self): return self.config.remove_section(self.section)
	
	def option_wrapper(self, option):
		return _OptionWrapper(self, option)
	
def section_wrapper(config, section):
	return _SectionWrapper(config, section)