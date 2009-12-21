'''
Created on Dec 10, 2009

@author: marat
'''

if __name__ == "__main__":

	import sys
	import os.path
	
	# Append src path to PYTHONPATH
	src_path = os.path.realpath(os.path.dirname(__file__) + "/../..")
	sys.path.append(src_path)
	
	from scalarizr.core import Bus, BusEntries
	import logging
	
	bus = Bus()
	config = bus[BusEntries.CONFIG]
	logger = logging.getLogger("scalarizr.core")
	
	logger.info("Starting scalarizr...")
	
	# Read behaviour configurations and inject them into global config
	from ConfigParser import ConfigParser
	from scalarizr.util import inject_config
	behaviour = config.get("default", "behaviour").split(",")
	for bh in behaviour:
		filename = "%s/etc/include/behaviour.%s.ini" % (bus[BusEntries.BASE_PATH], bh)
		if os.path.exists(filename):
			logger.debug("Read behaviour configuration file %s", filename)
			bh_config = ConfigParser()
			bh_config.read(filename)
			inject_config(config, bh_config)
	
	# Start messaging
	from scalarizr.messaging import MessageServiceFactory
	factory = MessageServiceFactory()
	try:
		service = factory.new_service(config.get("messaging", "adapter"), config.items("messaging"))
	except Exception, e:
		logger.exception(e)
		sys.exit("Cannot create messaging service adapter '%s'" % (config.get("messaging", "adapter")))
	
	producer = service.new_producer()
	bus[BusEntries.MESSAGE_PRODUCER] = producer
	
	consumer = service.new_consumer()
	bus[BusEntries.MESSAGE_CONSUMER] = consumer
	from scalarizr.core.handlers import MessageListener
	consumer.add_message_listener(MessageListener())
	try:
		consumer.start()
	except KeyboardInterrupt:
		logger.info("Stopping scalarizr...")
		consumer.stop()
		logger.info("Stopped")
