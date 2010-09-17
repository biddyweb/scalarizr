'''
Created on Sep 7, 2010

@author: marat
'''
from scalarizr.bus import bus
from scalarizr.libs.metaconf import Configuration
import os
import logging

class CnfPreset:
	name = None
	settings = None
	
	def __init__(self, name=None, settings=None):
		self.name = name
		self.settings = settings or {}

	def __repr__(self):
		return 'name = ' + str(self.name) \
	+ "; settings = " + str(self.settings)
		

class CnfPresetStore:
	class PresetType:
		DEFAULT = 'default'
		LAST_SUCCESSFUL = 'last_successful'
		CURRENT = 'current'
	
	def __init__(self):
		self._logger = logging.getLogger(__name__)
		cnf = bus.cnf
		self.presets_path = os.path.join(cnf.home_path, 'presets')
		if not os.path.exists(self.presets_path):
			try:
				os.makedirs(self.presets_path)
			except OSError,e:
				pass
	
	def _filename(self, service_name, preset_type):
		return os.path.join(self.presets_path,service_name + '.' + preset_type)
	
	def load(self, service_name, preset_type):
		'''
		@rtype: Preset
		@raise OSError, MetaconfError: 
		'''
		self._logger.debug('Loading %s %s preset' % (preset_type, service_name))
		ini = Configuration('ini')
		ini.read(self._filename(service_name, preset_type))
		return CnfPreset(ini.get('general/name'), ini.get_kv_dict('settings/')) 
		
	def save(self, service_name, preset, preset_type):
		'''
		@type service_name: str
		@type preset: CnfPreset
		@type preset_type: CnfPresetStore.PresetType
		'''
		if not preset or not hasattr(preset, 'settings'):
			self._logger.error('Cannot save preset: No settings in preset found.')
			return
		
		self._logger.debug('Saving preset as %s' % preset_type)
		ini = Configuration('ini')
		ini.add('general')
		ini.add('general/name', preset.name if (hasattr(preset, 'name') and preset.name) else 'Noname')
		ini.add('settings')
		print 'saving:', preset
		for k, v in preset.settings.items():
			ini.add('settings/%s' % k, v)
		ini.write(open(self._filename(service_name, preset_type), 'w'))
		
class CnfController(object):
	def current_preset(self):
		'''
		@rtype: CnfPreset
		'''
		pass

	def apply_preset(self, preset):
		'''
		@type preset: CnfPreset
		@raise:
		'''
		pass	

class Options:
	
	options = None 
	
	def __init__(self, *args):
		self.options = args
		
		for optspec in args:
			setattr(self, optspec.name, optspec)	