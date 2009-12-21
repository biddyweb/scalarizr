class BusEntries:
	BASE_PATH = "base_path"
	"""
	@cvar string: Application base path
	"""
	
	CONFIG = "config"
	"""
	@cvar ConfigParser.RawConfigParser: Scalarizr configuration 
	"""
	
	DB = "db"
	"""
	@cvar sqlalchemy.pool.SingletonThreadPool: Database connection pool
	"""
	
	MESSAGE_CONSUMER = "message_consumer"
	"""
	@cvar scalarizr.messaging.MessageConsumer: Default message consumer
	"""
	
	MESSAGE_PRODUCER = "message_producer"
	"""
	@cvar scalarizr.messaging.MessageProducer: Default message producer
	"""
	
	QUERYENV_SERVICE = "queryenv_service"
	"""
	@cvar: QueryEnv service client
	"""

class _Bus:
	_registry = {}
	
	def __setitem__(self, name, value):
		self._registry[name] = value
	
	def __getitem__(self, name):
		return self._registry[name]
	
	
# Bus singleton 	
_bus = None
def Bus ():
	global _bus
	if (_bus is None):
		_bus = _Bus()
	return _bus


import os.path
import sqlite3 as sqlite
import sqlalchemy.pool as pool
from ConfigParser import ConfigParser
import logging
import logging.config

	
def initialize():
	bus = Bus()
	bus[BusEntries.BASE_PATH] = os.path.realpath(os.path.dirname(__file__) + "/../../..")
	
	# Configure logging
	logging.config.fileConfig(bus[BusEntries.BASE_PATH] + "/etc/logging.ini")
	
	# Load configuration
	config = ConfigParser()
	config.read(bus[BusEntries.BASE_PATH] + "/etc/config.ini")
	bus[BusEntries.CONFIG] = config
	
	# Configure database connection pool
	bus[BusEntries.DB] = pool.SingletonThreadPool(_connect)

def _connect():
	bus = Bus()
	file = bus[BusEntries.BASE_PATH] + "/" + bus[BusEntries.CONFIG].get("default", "storage_path")

	logger = logging.getLogger(__package__)
	logger.debug("Open SQLite database (file: %s)" % (file))
	
	conn = sqlite.Connection(file)
	conn.row_factory = sqlite.Row
	return conn
	
initialize()
