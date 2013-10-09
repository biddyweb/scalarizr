
import uuid
import sys
import logging
import threading

from scalarizr import rpc, bus
from scalarizr.messaging import Queues, Messages

class OperationAPI(object):

    _instance = None

    def __new__(cls, *args, **kwds):
        if not cls._instance:
            cls._instance = super(OperationAPI, cls).__new__(cls, *args, **kwds)
        return cls._instance

    def __init__(self):
        self._ops = {}


    @rpc.service_method
    def result(self, operation_id=None):
        return self.get(operation_id).serialize()

    def create(self, name, func, **kwds):
        op = Operation(name, func, **kwds)
        self._ops[op.operation_id] = op
        return op

    def get(self, operation_id):
        # TODO: wrap KeyError
        return self._ops[operation_id]

    def find(self, name=None):
        return [op for op in self._ops.values() if op.name == name]

    def go_with(self, name, func, async=False, **kwds):
        op = self.create(name, func, **kwds)
        if async:
            op.start()
            return op.operation_id
        else:
            return op.execute()


class _LogHandler(logging.Handler):
    def __init__(self, op):
        self.op = op
        super(_LogHandler, self).__init__(self)

    def emit(self, message):
        self.op.logs.append(message)


class Operation(object):

    def __init__(self, name, func, **kwds):
        self.operation_id = str(uuid.uuid4())
        self.name = name
        self.func = func
        self.status = kwds.get('status', 'new')
        self.result = kwds.get('result')
        self.logs = kwds.get('logs', [])
        self.error = kwds.get('error')
        self.async = False
        self.thread = None
        self.logger = None
        self._init_log()

    def _init_log(self):
        self.logger = logging.getLogger('scalarizr.ops.{0}'.format(self.name))
        hdlr = _LogHandler(self)
        hdlr.setLevel(logging.INFO)
        self.logger.addHandler(hdlr)    

    def _run(self):
        try:
            self.complete(self.func(self))
        except:
            self.fail()
        finally:
            svs = bus.messaging_service
            msg = svs.new_message(Messages.OPERATION_RESULT, None, self.serialize())
            svs.get_producer().send(Queues.CONTROL, msg)

    def execute(self):
        self.status = 'in-progress'
        self._run()
        return self.result

    def start(self):
        self.thread = threading.Thread(
            name='Task {0}'.format(self.name), 
            target=self._run
        )
        self.async = True
        self.status = 'in-progress'
        self.thread.start()

    def fail(self, *exc_info):
        self.error = exc_info or sys.exc_info()
        self.status = 'failed'
        self.logger.exception('Operation "%s" (id: %s) failed', 
                self.name, self.operation_id, exc_info=self.error)

    def complete(self, result=None):
        self.result = result
        self.status = 'completed'

    def serialize(self):
        ret = {
            'id': self.operation_id,
            'status': self.status,
            'result': None,
            'error': None,
            'logs': 'todo: serialize {0} log entries'.format(len(self.logs))
        }
        if self.status == 'completed':
            ret['result'] = self.result
        else:
            ret['error'] =  'todo: serialize exception',
        return ret
