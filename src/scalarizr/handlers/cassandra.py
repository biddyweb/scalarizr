'''
Created on Jun 23, 2010

@author: marat
@author: Dmytro Korsakov
'''
from scalarizr.bus import bus
from scalarizr.config import BuiltinBehaviours
from scalarizr.handlers import Handler, HandlerError
from scalarizr.messaging import Messages
import logging
import os
import re
from scalarizr.util import  fstool, system, get_free_devname, filetool,\
	firstmatched
from scalarizr.util import initdv2
from scalarizr.util import iptables
from scalarizr.util.iptables import IpTables, RuleSpec
from ConfigParser import NoOptionError
from Queue import Queue, Empty
from threading import Timer
from scalarizr.libs.metaconf import Configuration, PathNotExistsError
import urllib2
import tarfile
import time
import pexpect
from datetime import datetime, timedelta


BEHAVIOUR = SERVICE_NAME = BuiltinBehaviours.CASSANDRA
CNF_SECTION 			= BEHAVIOUR
CNF_NAME 				= BEHAVIOUR

OPT_STORAGE_SIZE		= 'storage_size'
OPT_SNAPSHOT_URL		= 'snapshot_url'
OPT_STORAGE_CNF_URL		= 'storage_conf_url'
OPT_STORAGE_PATH 		= 'storage_path'
OPT_STORAGE_CNF 		= 'storage_conf'
OPT_SNAPSHOT_ID			= "snapshot_id"
OPT_STORAGE_VOLUME_ID	= "volume_id"
OPT_STORAGE_DEVICE_NAME	= "device_name"
TMP_EBS_MNTPOINT        = '/mnt/temp_storage'
CDB_TIMEOUT             = 60
CDB_MAX_ATTEMPTS        = 3
CRF_TIMEOUT				= 100
CRF_MAX_ATTEMPTS		= 3


class CassandraInitScript(initdv2.ParametrizedInitScript):
	def __init__(self):
		pl = bus.platform
		initd_script = "/etc/init.d/cassandra"
		if not os.path.exists(initd_script):
			raise HandlerError("Cannot find Cassandra init script at %s. Make sure that Cassandra is installed" % initd_script)
		
		pid_file = '/var/run/cassandra.pid'
		socks = [initdv2.SockParam(port, conn_address = pl.get_private_ip()) for port in (7000, 9160)]
		initdv2.ParametrizedInitScript.__init__(self, 'cassandra', initd_script, pid_file, socks=socks)

initdv2.explore('cassandra', CassandraInitScript)


class CassandraMessages:
	
	CHANGE_RF					= 'Cassandra_ChangeReplFactor'
	'''
	@ivar rf_changes list( dict( name = 'Keyspace1', rf = 3)) 
	'''
	CHANGE_RF_RESULT			= 'Cassandra_ChangeReplFactorResult'
	'''
	@ivar status ok|error
	@ivar last_error
	@ivar bundles list(dict(remote_ip=ip, timestamp=123454, snapshot_id=snap-434244))
	'''
	
	INT_CHANGE_RF				= 'Cassandra_IntChangeReplFactor'
	'''
	@ivar leader_host
	'''
	
	INT_CHANGE_RF_RESULT		= 'Cassandra_IntChangeReplFactorResult'
	'''
	@ivar status ok|error
	@ivar last_error
	'''
	
	CREATE_DATA_BUNDLE			= "Cassandra_CreateDataBundle"
	
	CREATE_DATA_BUNDLE_RESULT 	= "Cassandra_CreateDataBundleResult"
	'''
	@ivar status ok|error
	@ivar last_error
	@ivar bundles list(dict(status = ok|error, last_error = , remote_ip=ip, timestamp=123454, snapshot_id=snap-434244))
	'''
	
	INT_CREATE_DATA_BUNDLE 		= "Cassandra_IntCreateDataBundle"
	'''
	@ivar leader_host
	'''
	
	INT_CREATE_DATA_BUNDLE_RESULT = "Cassandra_IntCreateDataBundleResult"
	'''
	@ivar status ok|error
	@ivar last_error
	@ivar timestamp
	@ivar snapshot_id
	@ivar remote_ip
	'''

class StorageError(BaseException): pass

cassandra = None

def get_handlers ():
	globals()['cassandra'] = Cassandra()
	return [CassandraScalingHandler(), CassandraCdbHandler, CassandraCrfHandler]

class Cassandra(object):

	def __init__(self):
		self._logger = logging.getLogger(__name__)
		
		config = bus.config
		self.queryenv = bus.queryenv_service
		
		self.platform = bus.platform
		
		self.private_ip = self.platform.get_private_ip()
		self.zone = self.platform.get_avail_zone()
		self.inst_id = self.platform.get_instance_id()
		
		self.ini = bus.cnf.rawini
		
		self.role_name = self.ini.get(config.SECT_GENERAL, config.OPT_ROLE_NAME)

		self.storage_path = self.ini.get(CNF_SECTION, OPT_STORAGE_PATH)
		self.storage_conf = self.ini.get(CNF_SECTION, OPT_STORAGE_CNF)

		self.data_file_directory = self.storage_path + "/datafile"
		self.commit_log_directory = self.storage_path + "/commitlog"
		
		self.cassandra_conf = Configuration('cassandra')
		
		try:
			self.cassandra_conf.read(self.storage_conf)
		except:
			pass
		
		self._initd = initdv2.lookup('cassandra')
		
	def restart_service(self):
		self.stop_service()
		self.start_service()
				
	def stop_service(self):
		try:
			self._logger.info("Stopping Cassandra service")
			self._initd.stop()
			self._logger.debug("Cassandra service stopped")
		except:
			self._logger.error("Cannot stop Cassandra")
			raise

	def start_service(self):
		try:
			self._logger.info("Starting Cassandra service")
			self._initd.start()
			self._logger.debug("Cassandra service started")
		except:
			self._logger.error("Cannot start Cassandra")
			raise

	def create_snapshot(self, vol_id):
		ec2_conn = cassandra.platform.new_ec2_conn()
		desc = 'cassandra-backup_' + self.role_name + '_' + time.strftime('%Y%m%d%H%M%S')
		snapshot  = ec2_conn.create_snapshot(vol_id, desc)
		del(ec2_conn)
		return snapshot.id

	@property
	def hosts(self):
		ret = []
		roles = self.queryenv.list_roles(behaviour = BEHAVIOUR)
		for role in roles:
			for host in role.hosts:
				ret.append(host.internal_ip or host.external_ip)
		return tuple(ret)


	def get_node_queue(self):
		
		cassandra.start_service()
		queue = Queue()
		"""
		out, err = system('nodetool -h localhost ring')[0:2]
		if err:
			raise HandlerError('Cannot get node list: %s' % err)
		
		lines = out.split('\n')
		ip_re = re.compile('^(\d{1,3}(\.\d{1,3}){3})')
		for line in lines[1:]:
			if not line:
				continue
			
			result = re.search(ip_re , line)
			
			if result:
				self._logger.info('ADDING NODE %s TO QUEUE' % result.group(1))
				queue.put((result.group(1), 0))
		"""
		roles = cassandra.queryenv.list_roles(behaviour = "cassandra")
		
		# Fill seed list from queryenv answer
		for role in roles:
			for host in role.hosts:
				if host.internal_ip:
					queue.put((host.internal_ip, 0))
				else:
					queue.put((host.external_ip, 0))

		return queue

	def write_config(self):
		self.cassandra_conf.write(open(self.storage_conf,'w'))

class CassandraScalingHandler(Handler):
	
	_port = None

	def __init__(self):
		
		self._logger = logging.getLogger(__name__)
		self._iptables = IpTables()

		bus.on("init", self.on_init)
		bus.on("before_host_down", self.on_before_host_down)
		

	def on_init(self):
		bus.on("before_host_up", self.on_before_host_up)
		bus.on("host_init_response", self.on_host_init_response)
			
	def accept(self, message, queue, behaviour=None, platform=None, os=None, dist=None):
		return BuiltinBehaviours.CASSANDRA in behaviour and \
				( message.name == Messages.HOST_INIT or
				message.name == Messages.HOST_UP or
				message.name == Messages.HOST_DOWN or
				message.name == Messages.BEFORE_HOST_TERMINATE )
	
	def on_HostInit(self, message):
		if message.behaviour == BuiltinBehaviours.CASSANDRA:
			if not message.local_ip:
				ip = message.remote_ip
			else:
				ip = message.local_ip
			self._insert_iptables_rule(ip)

	def on_host_init_response(self, message):

		if not message.body.has_key("cassandra"):
			raise HandlerError("HostInitResponse message for Cassandra behaviour must have 'cassandra' property")
		self._logger.debug("Update cassandra config with %s", message.cassandra)
		self._update_config(message.cassandra)

	def on_before_host_up(self, message):

		cassandra.stop_service()
		# Getting storage conf from url
		cassandra.cassandra_conf = Configuration('cassandra')
		storage_conf_url = cassandra.ini.get(CNF_SECTION, OPT_STORAGE_CNF_URL)
		result = re.search('^s3://(.*?)/(.*?)$', storage_conf_url)
		if result:
			bucket_name = result.group(1)
			file_name   = result.group(2)
			s3_conn = cassandra.platform.new_s3_conn()
			bucket = s3_conn.get_bucket(bucket_name)
			key = bucket.get_key(file_name)
			key.open_read()
			cassandra.cassandra_conf.readfp(key)
			del(s3_conn)
		else:
			try:
				request = urllib2.Request(storage_conf_url)
				result  = urllib2.urlopen(request)
				cassandra.cassandra_conf.readfp(result)
			except urllib2.URLError, e:
				raise HandlerError('Cannot retrieve cassandra sConsistencyLeveltorage configuration: %s', str(e))
		
		try:
			self._port = cassandra.cassandra_conf.get('Storage/StoragePort')
		except:
			self._logger.error('Cannot determine storage port from configuration file')
			self._port = 7000
			# Adding port to config
			cassandra.cassandra_conf.add('Storage/StoragePort', self._port)
		
		# Clear Seed list
		cassandra.cassandra_conf.remove('Storage/Seeds/Seed')
		
		
		
		roles = cassandra.queryenv.list_roles(behaviour = "cassandra")
		ips = []
		# Fill seed list from queryenv answer and creating iptables rules
		for role in roles:
			for host in role.hosts:
				if host.internal_ip:
					ips.append(host.internal_ip)
				else:
					ips.append(host.external_ip)
		
		for ip in ips:
			cassandra.cassandra_conf.add('Storage/Seeds/Seed', ip)
			self._insert_iptables_rule(ip)
		
		self._insert_iptables_rule(cassandra.private_ip)
		self._drop_iptable_rules()
		
		
		
		no_seeds = False
		if not cassandra.cassandra_conf.get_list('Storage/Seeds/'):
			cassandra.cassandra_conf.add('Storage/Seeds/Seed', cassandra.private_ip)
			no_seeds = True
					
		cassandra.cassandra_conf.set('Storage/ListenAddress', cassandra.private_ip)
		cassandra.cassandra_conf.set('Storage/ThriftAddress', '0.0.0.0')
		
		# Temporary config write
		cassandra.write_config()

		# Determine startup type (server import, N-th startup, Scaling )
		try:
			cassandra.ini.get(CNF_SECTION, OPT_SNAPSHOT_URL)
			self._start_import_snapshot(message)
		except NoOptionError:
			try:
				cassandra.ini.get(CNF_SECTION, 'snapshot_id')
				self._start_from_snap(message)
			except NoOptionError:
				if no_seeds:
					raise HandlerError('Cannot start bootstrap without seeds')
				self._start_bootstrap(message)
				
				
	def _start_import_snapshot(self, message):
		try:
			storage_size = cassandra.ini.get(CNF_SECTION, OPT_STORAGE_SIZE)
			snapshot_url = cassandra.ini.get(CNF_SECTION, OPT_SNAPSHOT_URL)
			filename = os.path.basename(snapshot_url)
			snap_path = os.path.join(TMP_EBS_MNTPOINT, filename)
			result = re.search('^s3://(.*?)/(.*?)$', snapshot_url)
			# If s3 link
			if result:
				bucket_name = result.group(1)
				file_name   = result.group(2)
				s3_conn 	= cassandra.platform.new_s3_conn()
				bucket 		= s3_conn.get_bucket(bucket_name)
				key 		= bucket.get_key(file_name)
				if not key:
					raise HandlerError('File %s does not exist on bucket %s', (file_name, bucket_name))
				# Determine snapshot size in Gb			
				length = int(key.size//(1024*1024*1024) +1)
				
				if length > storage_size:
					raise HandlerError('Snapshot length (%s) is bigger then storage size (%s)' % (length, storage_size))
				
				temp_ebs_size = length*10 if length*10 < 1000 else 1000
				tmp_ebs_devname, temp_ebs_dev = self._create_attach_mount_volume(temp_ebs_size, auto_mount=False, mpoint=TMP_EBS_MNTPOINT)
				self._logger.debug('Starting download cassandra snapshot: %s', snapshot_url)			
				key.get_contents_to_filename(snap_path)
				
			# Just usual http or ftp link
			else:
				try:
					result   = urllib2.urlopen(snapshot_url)
				except urllib2.URLError:
					raise HandlerError('Cannot download snapshot. URL: %s' % snapshot_url)
				
				# Determine snapshot size in Gb
				try:
					length = int(int(result.info()['content-length'])//(1024*1024*1024) + 1)
				except:
					self._logger.error('Cannot determine snapshot size. URL: %s', snapshot_url)
					length = storage_size
					
				if length > storage_size:
					raise HandlerError('Snapshot length (%s) is bigger then storage size (%s)' % (length, storage_size))
				
				temp_ebs_size = length*10 if length*10 < 1000 else 1000			
				tmp_ebs_devname, temp_ebs_dev = self._create_attach_mount_volume(temp_ebs_size, auto_mount=False, mpoint=TMP_EBS_MNTPOINT)
				
				self._logger.debug('Starting download cassandra snapshot: %s', snapshot_url)
				
				try:
					fp = open(snap_path, 'wb')
				except (Exception, BaseException), e:
					raise HandlerError('Cannot open snapshot file %s for write: %s', (filename, str(e)))
				else:
					while True:
						data = result.read(4096)
						if not data:
							break
						fp.write(data)
				finally:		
					fp.close()
	
			self._logger.debug('Trying to extract cassandra snapshot to temporary EBS storage')
			snap = tarfile.open(snap_path)
			snap.extractall(TMP_EBS_MNTPOINT)
			snap.close()
			self._logger.debug('Snapshot successfully extracted')
			os.remove(snap_path)
			
			ebs_devname, ebs_volume = self._create_attach_mount_volume(storage_size, auto_mount=True, mpoint=cassandra.storage_path)
			self._update_config({OPT_STORAGE_VOLUME_ID : ebs_volume.id, OPT_STORAGE_DEVICE_NAME : ebs_devname})
			self._create_valid_storage()
			
			self._logger.debug('Copying snapshot')
			rsync = filetool.Rsync().archive()
			rsync.source(TMP_EBS_MNTPOINT+os.sep).dest(cassandra.data_file_directory)
			out = system(str(rsync))
	
			if out[2]:
				raise HandlerError('Error while copying snapshot content from temp ebs to permanent: %s', out[1])
	
			self._logger.debug('Snapshot successfully copied from temporary ebs to permanent')
	
			cassandra.cassandra_conf.set('Storage/AutoBootstrap', 'False')
	
			self._set_use_storage()
			snap_id = cassandra.create_snapshot(ebs_volume.id)
			cassandra.start_service()
			message.cassandra = dict(volume_id = ebs_volume.id, snapshot_id=snap_id)
		finally:
			try:
				self._umount_detach_delete_volume(tmp_ebs_devname, temp_ebs_dev)
				os.removedirs(TMP_EBS_MNTPOINT)
			except:
				pass


	def _start_from_snap(self, message):
		snap_id = cassandra.ini.get(CNF_SECTION, 'snapshot_id')
		ebs_devname, ebs_volume = self._create_attach_mount_volume(auto_mount=True, snapshot=snap_id, mpoint=cassandra.storage_path, mkfs=False)
		self._update_config({OPT_STORAGE_VOLUME_ID : ebs_volume.id, OPT_STORAGE_DEVICE_NAME : ebs_devname})

		self._create_valid_storage()
		self._set_use_storage()

		cassandra.start_service()
		message.cassandra = dict(volume_id = ebs_volume.id)

	def _start_bootstrap(self, message):
		
		storage_size = cassandra.ini.get(CNF_SECTION, OPT_STORAGE_SIZE)
		
		ebs_devname, ebs_volume = self._create_attach_mount_volume(storage_size, auto_mount=True, snapshot=None, mpoint=cassandra.storage_path)
		self._update_config({OPT_STORAGE_VOLUME_ID : ebs_volume.id, OPT_STORAGE_DEVICE_NAME : ebs_devname})
		self._create_valid_storage()

		cassandra.cassandra_conf.set('Storage/AutoBootstrap', 'True')
		self._set_use_storage()
		
		cassandra.start_service()
		self._logger.debug('Sleep 120 seconds because of http://wiki.apache.org/cassandra/Operations#line-57')

		# The new node will log "Bootstrapping" when this is safe, 2 minutes after starting.
		# http://wiki.apache.org/cassandra/Operations#line-57
		time.sleep(120)
		self._logger.debug('Waiting for bootstrap finish')
		self._wait_until(self._bootstrap_finished, sleep = 10)
		message.cassandra = dict(volume_id = ebs_volume.id)

	def on_HostUp(self, message):
		if message.behaviour == BuiltinBehaviours.CASSANDRA:
			
			if not message.local_ip:
				ip = message.remote_ip
			else:
				ip = message.local_ip
				
			seeds = cassandra.cassandra_conf.get_list('Storage/Seeds/')
			
			if not ip in seeds:
				cassandra.cassandra_conf.add('Storage/Seeds/Seed', ip)
				cassandra.write_config()
				cassandra.restart_service()
			
	def on_BeforeHostTerminate(self, *args):
		cassandra.start_service()
		err = system('nodetool -h localhost decommission')[2]
		if err:
			raise HandlerError('Cannot decommission node: %s' % err)
		self._wait_until(self._is_decommissioned)
		cassandra.stop_service()
		
	def on_before_host_down(self, *args):
		cassandra.stop_service()

	def _bootstrap_finished(self):
		try:
			cass = pexpect.spawn('nodetool -h localhost streams')
			out = cass.read()
			if re.search('Mode: Normal', out):
				return True
			return False
		finally:
			del(cass)
		
	def on_HostDown(self, message):
		
		if message.behaviour == BuiltinBehaviours.CASSANDRA:
			
			if not message.local_ip:
				ip = message.remote_ip
			else:
				ip = message.local_ip
				
			try:
				cassandra.cassandra_conf.remove('Storage/Seeds/Seed', ip)
				cassandra.write_config()
				self._del_iptables_rule(ip)
				cassandra.restart_service()
			except:
				pass
			


	def _insert_iptables_rule(self, ip):
		storage_rule = RuleSpec(protocol=iptables.P_TCP, dport='7000', jump='ACCEPT', source = ip)
		thrift_rule  = RuleSpec(protocol=iptables.P_TCP, dport='9160', jump='ACCEPT', source = ip)
		self._iptables.insert_rule(None, storage_rule)
		self._iptables.insert_rule(None, thrift_rule)
		
			
	def _del_iptables_rule(self, ip):
		storage_rule = RuleSpec(protocol=iptables.P_TCP, dport='7000', jump='ACCEPT', source = ip)
		thrift_rule  = RuleSpec(protocol=iptables.P_TCP, dport='9160', jump='ACCEPT', source = ip)
		self._iptables.delete_rule(storage_rule)
		self._iptables.delete_rule(thrift_rule)
			
	def _drop_iptable_rules(self):
		storage_rule = RuleSpec(protocol=iptables.P_TCP, dport='7000', jump='DROP')
		thrift_rule  = RuleSpec(protocol=iptables.P_TCP, dport='9160', jump='DROP')
		self._iptables.append_rule(storage_rule)
		self._iptables.append_rule(thrift_rule)

	def _wait_until(self, target, args=None, sleep=5):
		args = args or ()
		while not target(*args):
			self._logger.debug("Wait %d seconds before the next attempt", sleep)
			time.sleep(sleep)
			
	def _create_attach_mount_volume(self, size=None, auto_mount=False, snapshot=None, mpoint=None, mkfs=True):
		
		if not size and not snapshot:
			raise HandlerError('Cannot create volume without size or snapshot')

		if not cassandra.zone:
			cassandra.zone     = cassandra.platform.get_avail_zone()
		if not cassandra.inst_id:
			cassandra.inst_id  = cassandra.platform.get_instance_id()
		
		ec2_conn = cassandra.platform.new_ec2_conn()
		self._logger.debug('Creating new EBS volume')
		ebs_volume = ec2_conn.create_volume(size, cassandra.zone, snapshot)
		
		self._logger.debug('Waiting for new ebs volume. ID=%s', (ebs_volume.id,))
		self._wait_until(lambda: ebs_volume.update() == "available")
		
		device   = get_free_devname()
		
		self._logger.debug('Attaching volume ID=%s', (ebs_volume.id,))		
		ebs_volume.attach(cassandra.inst_id, device)
		self._wait_until(lambda: ebs_volume.update() and ebs_volume.attachment_state() == "attached")
	
		if not os.path.exists(mpoint):
			os.makedirs(mpoint)
			
		fstool.mount(device, mpoint, make_fs=mkfs, auto_mount=auto_mount )
		
		del(ec2_conn)
		
		return device, ebs_volume
	
	def _umount_detach_delete_volume(self, devname, volume):

		fstool.umount(devname)
		
		volume.detach()
		self._wait_until(lambda: volume.update() and volume.volume_state() == "available")
		volume.delete()
		
	def _create_valid_storage(self):
		if not os.path.exists(cassandra.data_file_directory):
			os.makedirs(cassandra.data_file_directory)
		if not os.path.exists(cassandra.commit_log_directory):
			os.makedirs(cassandra.commit_log_directory)
		
	def _set_use_storage(self):

		self._config.set('Storage/CommitLogDirectory', cassandra.commit_log_directory)
		self._config.remove('Storage/DataFileDirectories/DataFileDirectory')
		self._config.add('Storage/DataFileDirectories/DataFileDirectory', cassandra.data_file_directory)

		cassandra.write_config()
	
	def _is_decommissioned(self):
		try:
			cass = pexpect.spawn('nodetool -h localhost streams')
			out = cass.read()
			if re.search('Decommissioned', out):
				return True
			return False
		finally:
			del(cass)
			
	def _update_config(self, data): 
		cnf = bus.cnf
		cnf.update_ini(BuiltinBehaviours.CASSANDRA, {CNF_SECTION: data})
		
		
class OnEachRunnable:
	command_name = None
	command_message = None	
	node_request_message = None
	node_response_message = None
	
	def create_node_request(self):
		'''
		Create node request message
		@rtype: scalarizr.messaging.Message
		'''
		pass
		
	def create_node_response(self):
		'''
		Create node response message
		@rtype: scalarizr.messaging.Message
		'''
		pass
	
	def create_command_result(self):
		'''
		Create ring result message 
		@rtype: scalarizr.messaging.Message
		'''
		pass
	
	def handle_request(self, req_message, resp_message):
		'''
		Perform work on node and fill resp_message with results.
		'''
		pass	
	
		
class OnEachExecutor(Handler):
	_logger = None
	
	RESP_OK = 'ok'
	RESP_ERROR = 'error'

	request_timeout = 60
	'''
	Request timeout in seconds
	'''
	
	resend_interval = 5
	'''
	Request resend interval in seconds
	'''

	class NodeData:
		index = None
		host = None
		num_attempts = None		
		last_attempt_time = None
		req_message = None
		req_ok = None		
		timer = None
	
	class Context:
		nodes = None
		queue = None
		current_node = None
		timeframe = None
		start_time = None
		request_timeout = None
		request_base_data = None
		resend_interval = None
		results = None
		
		def __init__(self, **params):
			if params:
				for key, value in params.items():
					if hasattr(self, key):
						setattr(self, key, value)
		
	context = None
		
	runnable = None
	'''
	@type runnable: OnEachRunnable
	'''

	def __init__(self, runnable, **params):
		self._logger = logging.getLogger(__name__)
		
		self.runnable = runnable
		if params:
			for key, value in params.items():
				if hasattr(self, key):
					setattr(self, key, value)
	
	def accept(self, message, queue, behaviour=None, platform=None, os=None, dist=None):
		r = self.runnable
		return BuiltinBehaviours.CASSANDRA in behaviour \
				and message.name in (r.node_request_message, r.node_response_message, r.command_message)


	def _handle_control(self, contol_message):
		if self.context:
			start_dt = datetime.fromtimestamp(self.context.start_time)
			end_dt = start_dt + timedelta(seconds=self.context.timeframe)
			self._exception('Command %s is already running. (start: %s, estimated end: %s)' % (
				self.runnable.command_name, 
				start_dt.strftime("%Y-%m-%d %H:%M"), 
				end_dt.strftime("%Y-%m-%d %H:%M")
			))
			return

		try:
			hosts = cassandra.hosts
			timeframe = int(len(hosts)*self.request_timeout*1.2) # 120% of time when all nodes reach timeout  
			nodes = []
			queue = Queue(len(hosts))
			results = []
			i = 0
			for host in hosts:
				ndata = self.NodeData()
				ndata.index = i
				ndata.host = host
				ndata.num_attempts = 0
				ndata.last_attempt_time = 0
				req = self.runnable.create_node_request(self)
				req.body.update(self.context.request_base_data)
				ndata.req_message = req
				
				nodes.append(ndata)
				queue.put(ndata.index)				
				results.append(dict(
					status = self.RESP_ERROR,
					last_error = 'Command was not sent do to timeout (%d seconds)' % timeframe,
					host = ndata.host
				))
				i += 1
			
			ctx = self.Context(
				nodes = nodes,
				queue = queue,
				timeframe = timeframe,
				start_time = time.time(),
				request_timeout = self.request_timeout,
				request_base_data = dict(leader_host=cassandra.private_ip),
				resend_interval = self.resend_interval,
				results = {}
			)
			self.context = ctx

		except (BaseException, Exception), e:
			self._exception(None, e)
			return

		self._request_next_node()


	def _request_next_node(self):
		try:
			if time.time() - self.context.start_time >= self.context.timeframe:
				raise StopIteration('Command stopped due to timeout (%d seconds)' % self.context.timeframe)

			nindex = self.context.queue.get()
			ndata = self.context.nodes[nindex]
			t = time.time() - ndata.last_attempt_time
			if t < self.context.resend_interval:
				time.sleep(self.context.resend_interval - t)

			ndata.last_attempt_time = time.time()
			ndata.num_attempts += 1
			self.context.current_node = ndata
			ndata.timer = Timer(self.context.request_timeout, self._request_timeouted, (ndata,))
			ndata.timer.start()

			try:
				self.send_int_message(ndata.host, ndata.req_message)
			except (BaseException, Exception), e:
				self._logger.debug('Cannot deliver message %s to node %s. Reset timer and put node to the end. %s', 
						ndata.req_message.name, ndata.host, e)
				self._request_failed(ndata)
	
		except (Empty, StopIteration), e:
			self._result(e if isinstance(e, StopIteration) else None)


	def _request_timeouted(self, ndata):
		err = 'Request %s to node %s timeouted (%d seconds). Number of attempts: %s' % (
				ndata.req_message.name, ndata.host, self.context.request_timeout, ndata.num_attempts)
		self.context.results[ndata.index]['last_error'] = err
		self._logger.warning(err)
		
		self.context.current_node = None
		self.context.queue.put(ndata.index)
	
	def _request_failed(self, ndata=None):
		self._stop_node_timer(ndata)
		
		err = 'Request %s to node %s failed. Number of attempts: %s' % (
			ndata.req_message.name, ndata.host, self.context.request_timeout, ndata.num_attempts)
		self.context.results[ndata.index]['last_error'] = err
		self._logger.warning(err)
		
		self.context.current_node = None
		self.context.queue.put(ndata.index)

	def _stop_node_timer(self, ndata):
		if ndata.timer:
			try:
				ndata.timer.cancel()
			except:
				pass
			ndata.timer = None
	
	def _stop_current_node(self, ndata):
		if self.context.current_node:
			self._stop_node_timer(self.context.current_node)
			self.context.current_node = None

	def _handle_response(self, resp_message):
		current_node_respond = resp_message.from_host == self.context.current_node.host
		ndata = firstmatched(lambda n: n.host == resp_message.from_host, self.context.nodes)
		if not ndata:
			self._logger.error('Recived response %s from unknown node %s', 
					resp_message.name, getattr(resp_message, 'from_host', '*unknown*'))
			return
		
		if current_node_respond:
			self._stop_node_timer(ndata)
			
		self.context.results[ndata.index] = resp_message.body
		ndata.req_ok = resp_message.status == self.RESP_OK
		if self.RESP_ERROR == resp_message.status:
			self.context.queue.put(ndata.index)
			
		self._request_next_node()


	def _exception(self, message=None, e=None):
		msg = self.runnable.create_command_result(self)
		msg.status = self.RESP_ERROR
		msg.last_error = '%s%s' % (message and str(message) + '. ' or '', e or '')
		self.send_message(msg)


	def _result(self, e=None):
		try:
			msg = self.runnable.create_command_result(self)
			msg.status = all(lambda n: n.req_ok, self.context.nodes) and self.RESP_OK or self.RESP_ERROR
			
			if e:
				msg.last_error = str(e)
			else:
				failed_row = firstmatched(lambda row: row['last_error'], self.context.results)
				if failed_row:
					msg.last_error = failed_row['last_error']
					
			msg.rows = self.context.results
			self.send_message(msg)
		finally:
			self.context = None
		
	def __call__(self, message):
		if message.name == self.runnable.command_message:
			self._handle_control(message)
		elif message.name == self.runnable.node_response_message:
			if self.context:
				self._handle_response(message)
			else:
				self._logger.warning('Recived response %s from node %s when timeframe is already closed', 
						message.name, getattr(message, 'from_host', '*unknown*'))
		
		elif message.name == self.runnable.node_request_message:
			resp_message = self.runnable.create_node_response(self)
			resp_message.from_host = cassandra.private_ip
			try:
				self.runnable.handle_request(message, resp_message)
			except (BaseException, Exception), e:
				resp_message.status = self.RESP_ERROR
				resp_message.last_error = str(e)
			self.send_int_message(message.leader_host, resp_message)



class _CassandraCdbRunnable(OnEachRunnable):
	
	command_name = 'data bundle'
	command_message = CassandraMessages.CREATE_DATA_BUNDLE
	command_result_message = CassandraMessages.CREATE_DATA_BUNDLE_RESULT		
	node_request_message = CassandraMessages.INT_CREATE_DATA_BUNDLE
	node_response_message = CassandraMessages.INT_CREATE_DATA_BUNDLE_RESULT

	def create_node_request(self, handler):
		'''
		Create node request message
		@rtype: scalarizr.messaging.Message
		'''
		return handler.new_message(self.node_response_message, include_pad=True)
		
	def create_node_response(self, handler):
		'''
		Create node response message
		@rtype: scalarizr.messaging.Message
		'''
		return handler.new_message(self.node_response_message)
	
	def create_command_result(self, handler):
		'''
		Create ring result message 
		@rtype: scalarizr.messaging.Message
		'''
		return handler.new_message(self.command_result_message)
	
	def handle_request(self, req_message, resp_message):
		'''
		Perform work on node and fill resp_message with results.
		'''
		umounted = False
		device_name = cassandra.ini.get(CNF_SECTION, OPT_STORAGE_DEVICE_NAME)
		
		try:
			cassandra.stop_service()
			system('sync')			
						
			fstool.umount(device_name)
			umounted = True
			
			volume_id = cassandra.ini.get(CNF_SECTION, OPT_STORAGE_VOLUME_ID)
			resp_message.body.update(dict(
				snapshot_id = cassandra.create_snapshot(volume_id),
				timestamp = time.strftime('%Y-%m-%d %H-%M')
			))

		finally:
			if umounted:
				fstool.mount(device_name, cassandra.storage_path)
			cassandra.start_service()
			
CassandraCdbHandler = OnEachExecutor(_CassandraCdbRunnable())	

class _CassandraCrfRunnable(OnEachRunnable):
	
	command_name = 'change replication factor'
	command_message = CassandraMessages.CHANGE_RF
	command_result_message = CassandraMessages.CHANGE_RF_RESULT		
	node_request_message = CassandraMessages.INT_CHANGE_RF
	node_response_message = CassandraMessages.INT_CHANGE_RF_RESULT

	def create_node_request(self, handler):
		return handler.new_message(self.node_response_message, include_pad=True)
		
	def create_node_response(self, handler):
		return handler.new_message(self.node_response_message)
	
	def create_command_result(self, handler):
		return handler.new_message(self.command_result_message)
	
	def handle_request(self, req_message, resp_message):
		
		def cleanup(self):
			err = system('nodetool -h localhost cleanup')[2]
			if err: 
				raise HandlerError('Cannot do cleanup: %s' % err)
		
		def repair(self, keyspace):
			err = system('nodetool -h localhost repair %s' % keyspace)[2]
			if err: 
				raise HandlerError('Cannot do cleanup: %s' % err)

		increase_ks_list = list()
		changes = req_message.rf_changes
		for keyspace in changes:
			
			keyspace_name = keyspace['name']
			new_rf		  = keyspace['rf']
			
			try:
				rf = cassandra.cassandra_conf.get("Storage/Keyspaces/Keyspace[@Name='"+keyspace_name+"']/ReplicationFactor")
			except PathNotExistsError:
				raise HandlerError('Keyspace %s does not exist or configuration file is broken' % keyspace_name)
			
			if rf < new_rf:
				increase_ks_list.append(keyspace_name)
			cassandra.cassandra_conf.set("Storage/Keyspaces/Keyspace[@Name='"+keyspace_name+"']/ReplicationFactor", new_rf)
			
		cassandra.write_config()
		cassandra.restart_service()

		cleanup()
		for keyspace in increase_ks_list:
			repair(keyspace)


CassandraCrfHandler = OnEachExecutor(_CassandraCrfRunnable())	


"""
class StorageProvider(object):
	
	_providers = None
	_instance = None
	
	def __new__(cls):
		if cls._instance is None:
			o = object.__new__(cls)
			o._providers = dict()
			cls._instance = o
		return cls._instance
	
	def new_storage(self, name, *args, **kwargs):
		if not name in self._providers:
			raise StorageError("Cannot create storage from undefined provider '%s'" % (name,))
		return self._providers[name](*args, **kwargs) 
	
	def register_storage(self, name, cls):
		if name in self._providers:
			raise StorageError("Storage provider '%s' already registered" % (name,))
		self._providers[name] = cls
		
	def unregister_storage(self, name):
		if not name in self._providers:
			raise StorageError("Storage provider '%s' is not registered" % (name,))
		del self._providers[name]

class Storage(object):
	def __init__(self):
		pass

	def init(self, mpoint, *args, **kwargs):
		pass

	def copy_data(self, src, *args, **kwargs):
		pass

class EbsStorage(Storage):
	pass

class EphemeralStorage(Storage):
	_platform = None
	def __init__(self):
		self._platform = bus.platform
		self._logger = logging.getLogger(__name__)

	def init(self, mpoint, *args, **kwargs):
		devname = '/dev/' + self._platform.get_block_device_mapping()["ephemeral0"]

		try:
			self._logger.debug("Trying to mount device %s and add it to fstab", devname)
			fstool.mount(device = devname, mpoint = mpoint, options = ["-t auto"], auto_mount = True)
		except fstool.FstoolError, e:
			if fstool.FstoolError.NO_FS == e.code:
				self._logger.debug("Trying to create file system on device %s, mount it and add to fstab", devname)
				fstool.mount(device = devname, mpoint = mpoint, options = ["-t auto"], make_fs = True, auto_mount = True)
			else:
				raise

	def copy_data(self, src, *args, **kwargs):
		pass

StorageProvider().register_storage("eph", EphemeralStorage)
"""	