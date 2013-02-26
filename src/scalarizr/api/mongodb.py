'''
Created on Feb 25, 2011

@author: uty
'''


from scalarizr.util.cryptotool import pwgen
from scalarizr.services import mongodb as mongodb_svc


class MongoDBAPI:

    def reset_password(self):
        """ Reset password for Mongo user 'scalr'. Return new password  """
        new_password = pwgen(10)
        mdb = mongodb_svc.MongoDB()
        mdb.cli.create_or_update_admin_user(mongodb_svc.SCALR_USER,
                                            new_password)
        return new_password
