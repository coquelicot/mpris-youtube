#!/usr/bin/python

# Copyright (C) 2013 Fcrh <coquelicot1117@gmail.com>
#
# mpris-youtube is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# mpris-youtube is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with mpris-youtube.  If not, see <http://www.gnu.org/licenses/>.

import os
import datetime
import httplib2
from optparse import OptionParser
from apiclient.discovery import build
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.tools import run
from oauth2client.keyring_storage import Storage

class Struct:

    def __init__(self, **kargs):
        self.__dict__.update(**kargs)

    def __repr__(self):
        return '{' + ', '.join([repr(key) + ': ' + repr(val) for key, val in self.__dict__.items()]) + '}'

class Logger:

    ENABLE_DEBUG = True
    ENABLE_INFO = True
    ENABLE_WARNING = True

    def __init__(self, name='', parent=None):
        self.name = name + ('.' + parent.name if parent is not None else "")

    def log(self, cat, msg):
        print "[%s] %s:%s > %s" % (datetime.datetime.now().strftime("%Y/%m/%d-%H:%M:%S"), self.name, cat, msg)

    def info(self, msg):
        if Logger.ENABLE_INFO:
            self.log('INFO', msg)

    def error(self, msg):
        self.log('ERROR', msg)

    def warning(self, msg):
        if Logger.ENABLE_WARNING:
            self.log('WARNING', msg)

    def debug(self, msg):
        if Logger.ENABLE_DEBUG:
            self.log('DEBUG', msg)

class APIService:

    YOUTUBE_API_SERVICE_NAME = "youtube"
    YOUTUBE_API_VERSION = "v3"

    DEVELOPER_KEY = "AIzaSyAthY54dVayuR5sSdW5hiOPwRAGEkUF1tM"
    CLIENT_ID='544447176625.apps.googleusercontent.com'
    CLIENT_SECRET='sM1_c9yLLaqabk6iu4sMm30o'
    AUTH_SCOPE='https://www.googleapis.com/auth/youtube'

    __auth_instance = None
    __instance = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, developerKey=DEVELOPER_KEY)

    @classmethod
    def instance(cls, authenticate=False):
        if not authenticate:
            return cls.__instance

        if cls.__auth_instance is None:
            storage = Storage('mpris-youtube', os.getlogin())
            credentials = storage.get()

            if credentials is None:
                flow = OAuth2WebServerFlow(
                        client_id=cls.CLIENT_ID,
                        client_secret=cls.CLIENT_SECRET,
                        scope=AUTH_SCOPE
                        #redirect_uri='urn:ietf:wg:oauth:2.0:oob'
                        )
                credentials = run(flow, storage)

            http = httplib2.Http()
            credentials.authorize(http)
            cls.__auth_instance = build(
                    cls.YOUTUBE_API_SERVICE_NAME,
                    cls.YOUTUBE_API_VERSION,
                    http=http)

            cls.__authenticate = True

        return cls.__auth_instance

class Cacher:

    def __init__(self, config):
        self.storageDir = config['storageDir']
        self.logger = Logger('Cacher')

    def getFile(self, fileName):
        self.logger.warning('Cacher not implemented')

class DBusInterface:

    def __init__ (self, MpYt):
        self.MpYt = MpYt

class MprisYoutube:

    def __init__(self, options):
        self.__dict__.update(options.__dict__)
        self.logger = Logger('MprisYoutube')

        self.loadConfig()
        self.cacher = Cacher(self.config)
        self.dbusInterface = DBusInterface(self)

    def loadConfig(self):

        # setup default configure
        self.config = dict()
        self.config["storageDir"] = os.path.join(os.environ['HOME'], '.fcrh', 'mpris-youtube', 'data')

        try:
            with open(self.configFile, 'r') as fin:
                for line in fin.readlines():
                    key, value = line.split('=', 1)
                    self.config[key.strip()] = value.strip()
            self.logger.info("Config loaded.")
        except:
            self.logger.warning("Can't load config file `%s', using default config." % self.configFile)

        self.logger.debug('config: ' + repr(self.config))

    def saveConfig(self):

        if not os.path.isfile(self.configFile):
            self.logger.info("Config file `%s' doesn't exist, creating one." % self.configFile)
            os.makedirs(os.path.dirname(self.configFile))

        try:
            with open(self.configFile, 'w') as fout:
                for key, value in self.config.items():
                    print >>fout, key + '=' + value
            self.logger.info("Config saved.")
        except:
            self.logger.error("Can't save config.")

def main():

    parser = OptionParser()
    parser.add_option('--config',
            dest="configFile",
            help="Specify the config file",
            default=os.path.join(os.environ['HOME'], '.fcrh', 'mpris-youtube', 'conf.txt'))
    (options, args) = parser.parse_args()

    MpYt = MprisYoutube(options)

if __name__ == "__main__":
    main()
