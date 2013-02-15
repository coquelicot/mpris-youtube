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

import Queue

import os
import sys
import time
import datetime
import thread
import threading
import subprocess
import httplib2
from optparse import OptionParser

import wave
import pyaudio

from apiclient.discovery import build
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.tools import run
from oauth2client.keyring_storage import Storage

import gobject
import dbus
import dbus.service
import dbus.mainloop.glib

class Struct:

    def __init__(self, **kargs):
        self.__dict__.update(**kargs)

    def __repr__(self):
        return '{' + ', '.join([repr(key) + ': ' + repr(val) for key, val in self.__dict__.items()]) + '}'

class Config:

    CONFIGFILE = os.path.join(os.environ['HOME'], '.fcrh', '.mpris-youtube', 'conf.txt')

    def __init__(self):

        # setup default configure
        config = dict()
        config["storageDir"] = os.path.join(os.environ['HOME'], '.fcrh', 'mpris-youtube', 'data')
        config["fetchThreads"] = 3

        try:
            with open(Config.CONFIGFILE, 'r') as fin:
                for line in fin.readlines():
                    key, value = line.split('=', 1)
                    #FIXME: what if it's a integer?
                    config[key.strip()] = value.strip()
            print 'Config loaded.'
        except:
            print "Can't load config file `%s', using default config." % Config.CONFIGFILE

        self.__dict__ = config

    def saveConfig(self):

        if not os.path.isfile(CONFIG.CONFIGFILE):
            print "Config file `%s' doesn't exist, creating one." % Config.CONFIGFILE
            os.makedirs(os.path.dirname(Config.CONFIGFILE))

        try:
            with open(CONFIG.CONFIGFILE, 'w') as fout:
                for key, value in self.__dict__.items():
                    print >>fout, key + '=' + value
            print "Config saved."
        except:
            print "Can't save config."

config = Config()

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

class FileManager:

    EXTENTIONS = ['wav']
    DOWNLOAD_URI = 'http://www.youtube.com/watch?v=%s'

    fetchSet = set()
    lock = threading.Lock()

    class _fetcher(threading.Thread):

        idCnt = 0
        requests = Queue.Queue()
        cond = threading.Condition()
        fnull = open(os.devnull, 'w')

        def __init__(self):
            threading.Thread.__init__(self)
            self.daemon = True

            self.videoId = None
            FileManager._fetcher.idCnt += 1
            self.logger = Logger('_fetcher%d' % FileManager._fetcher.idCnt)
            self.logger.info('init')

        def run(self):

            while True:

                FileManager._fetcher.cond.acquire()
                while FileManager._fetcher.requests.empty():
                    FileManager._fetcher.cond.wait()
                self.videoId = FileManager._fetcher.requests.get()
                FileManager._fetcher.cond.release()
                self.logger.info('start to fetch %s' % self.videoId)

                prog = [
                    'youtube-dl',
                    '--quiet',
                    '--prefer-free-formats',
                    FileManager.DOWNLOAD_URI % self.videoId,
                    '-o', os.path.join(config.storageDir, 'video', '%(id)s.%(ext)s'),
                    '-x', '--audio-format', 'wav']
                code = subprocess.call(prog, stdout=FileManager._fetcher.fnull, stderr=FileManager._fetcher.fnull)

                if code == 0:
                    self.logger.info("video %s fetched" % self.videoId)
                else:
                    if code < 0:
                        self.logger.warning('Youtube-dl killed by signal %d' % -code)
                    elif code > 0:
                        self.logger.warning("Youtube-dl doesn't return 0!!")
                    with FileManager.lock:
                        FileManager.fetchSet.remove(self.videoId)

    def __init__(self):
        self.logger = Logger('FileManager')
        FileManager.fetchSet = self.loadSet()
        while FileManager._fetcher.idCnt < config.fetchThreads:
            FileManager._fetcher().start()

    def fetchVideo(self, videoId):
        if videoId not in self.fetchSet:
            FileManager._fetcher.cond.acquire()
            FileManager.fetchSet.add(videoId)
            FileManager._fetcher.requests.put(videoId)
            FileManager._fetcher.cond.notify()
            FileManager._fetcher.cond.release()

    def getVideo(self, videoId):
        with FileManager.lock:
            if videoId not in FileManager.fetchSet:
                raise RuntimeError("Video not in cache set.")

        while True:
            for ext in FileManager.EXTENTIONS:
                path = os.path.join(config.storageDir, 'video', videoId + '.' + ext)
                if os.path.isfile(path):
                    return wave.open(path, 'rb')

            time.sleep(10)
            self.logger.info('Waiting for file to be download..')

    def loadSet(self):
        result = set()
        for fileName in os.listdir(os.path.join(config.storageDir, 'video')):
            videoId, ext = fileName.rsplit('.', 1)
            if ext in FileManager.EXTENTIONS:
                result.add(videoId)
        return result

class DBusInterface(dbus.service.Object):

    NAME = "org.fcrh.MpYt"
    PATH = "/org/mpris/MediaPlayer2"
    IFACE_MAIN = "org.mpris.MediaPlayer2"
    IFACE_PLAYER = "org.mpris.MediaPlayer2.Player"
    #IFACE_PLAYLIST = "org.mpris.MediaPlayer2.Playlists"
    IFACE_PROPERTY = "org.freedesktop.DBus.Properties"

    def __init__ (self, MpYt):
        self.MpYt = MpYt
        self.logger = Logger('DBusInterface')

    # org.mpris.MediaPlayer2
    @dbus.service.method(IFACE_MAIN)
    def Raise(self):
        raise RuntimeError("Don't have a gui yet.")

    @dbus.service.method(IFACE_MAIN)
    def Quit(self):
        self.logger.info('Quit')
        sys.exit(0)

    # org.mpris.MediaPlayer2.Player
    @dbus.service.method(IFACE_PLAYER)
    def Next(self):
        if self.MpYt.player.props['CanGoNext']:
            raise NotImplementedError('Next')

    @dbus.service.method(IFACE_PLAYER)
    def Previous(self):
        if self.MpYt.player.props['CanGoPrevious']:
            raise NotImplementedError('Previous')

    @dbus.service.method(IFACE_PLAYER)
    def Pause(self):
        if self.MpYt.player.props['CanPause']:
            raise NotImplementedError('Pause')

    @dbus.service.method(IFACE_PLAYER)
    def PlayPause(self):
        if self.MpYt.player.props['CanPlayPause']:
            raise NotImplementedError('PlayPause')
        else:
            raise RuntimeError('Error')

    @dbus.service.method(IFACE_PLAYER)
    def Stop(self):
        if self.MpYt.player.props['CanControl']:
            raise NotImplementedError('Stop')
        else:
            raise RuntimeError('Error')

    @dbus.service.method(IFACE_PLAYER)
    def Play(self):
        if self.MpYt.player.props['CanPlay']:
            raise NotImplementedError('Play')

    @dbus.service.method(IFACE_PLAYER, in_signature='x')
    def Seek(self, offset):
        if self.MpYt.player.props['CanSeek']:
            raise NotImplementedError('Seek')

    @dbus.service.method(IFACE_PLAYER, in_signature='ox')
    def SetPosition(self, trackId, position):
        if self.MpYt.player.props['CanSeek']:
            raise NotImplementedError('SetPosition')

    @dbus.service.method(IFACE_PLAYER, in_signature='s')
    def OpenUri(self, uri):
        raise RuntimeError('Error')

    @dbus.service.signal(IFACE_PLAYER, signature='x')
    def Seeked(self, position):
        pass

    # org.freedesktop.DBus.Properties
    @dbus.service.method(IFACE_PROPERTY, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        if interface == IFACE_MAIN:
            return self.MpYt.props
        elif interface == IFACE_PLAYER:
            return self.MpYt.player.props
        else:
            raise RuntimeError("Key Error")

class UserInterface(threading.Thread):

    def __init__(self, MpYt):
        threading.Thread.__init__(self)
        self.MpYt = MpYt
        self.playlistCache = dict()

    def getPlaylists(self):
        result = self.MpYt.getLists()
        for item in result:
            self.playlistCache[item["title"]] = item
        return result

    def getPlaylist(self, title=None):
        if title not in self.playlistCache:
            self.getPlaylists()
        if title in self.playlistCache:
            return self.playlistCache[title]
        else:
            return None

    def run(self):

        while True:
            cmd = raw_input('>> ').split()
            if cmd[0] == 'playlist.list':
                print ', '.join([item["title"] for item in self.getPlaylists()])
            elif cmd[0] == 'playlist.play':
                listId = self.getPlaylist(cmd[1])["id"]
                self.MpYt.player.setPlaylist(self.MpYt.getItems(listId, authenticate=False))
                self.MpYt.player.play()
            elif cmd[0] == 'playlistItem.list':
                listId = self.getPlaylist(cmd[1])["id"]
                print ', '.join([item["title"] for item in self.MpYt.getItems(listId, authenticate=False, part="snippet")])
            elif cmd[0] == 'current.next':
                self.MpYt.player.next()
            elif cmd[0] == 'current.prev':
                self.MpYt.player.prev()
            elif cmd[0] == 'current.pause':
                self.MpYt.player.pause()
            elif cmd[0] == 'current.play':
                self.MpYt.player.play()
            elif cmd[0] == 'current.stop':
                self.MpYt.player.stop()
            elif cmd[0] == 'current.seek':
                self.MpYt.player.seek(int(cmd[1]))
            elif cmd[0] == 'exit':
                sys.exit(0)

class Player:

    audio = pyaudio.PyAudio()

    class _player(threading.Thread):

        def __init__ (self, wav, lock, update, finish):
            threading.Thread.__init__(self)
            self.daemon = True

            self.wav = wav
            self.lock = lock
            self.playing = True
            self.terminate = False
            self.update = update
            self.finish = finish
            self.stream = Player.audio.open(
                    format=Player.audio.get_format_from_width(wav.getsampwidth()),
                    channels=wav.getnchannels(),
                    rate=wav.getframerate(),
                    output=True)

            self.start()

        def run(self):
            while True:
                with self.lock:
                    if self.terminate:
                        break
                    elif not self.playing:
                        if self.stream.is_active():
                            self.stream.stop_stream()
                        time.sleep(0.3)
                    else:
                        if self.stream.is_stopped():
                            self.stream.start_stream()
                        data = self.wav.readframes(1024)
                        if data == '':
                            self.terminate = True
                            self.finish()
                        else:
                            self.stream.write(data)
                            self.update()

            self.stream.close()

    def __init__(self, MpYt):
        self.MpYt = MpYt
        self.idx = 0
        self.playlist = []
        self._curPlayer = None
        self.lock = threading.Lock()
        self.logger = Logger('Player')

        self.props = dict(
                PlaybackStatus='Stopped', # Playing, Paused, Stopped
                #LoopStatusk='None', # None, Track, Playlist
                Rate=1.0, # only 1.0 for now
                #Shuffle=False,
                Metadata={},
                Volume=1.0,
                Position=0,
                MinimumRate=1.0,
                MaximumRate=1.0,
                CanGoNext=False,
                CanGoPrevious=False,
                CanPlay=False,
                CanPause=False,
                CanSeek=False,
                CanControl=True)

    def updateProps(self):
        self.logger.debug('updateProps')
        self.props["CanGoNext"] = self.idx < len(self.playlist) - 1
        self.props["CanGoPrevious"] = self.idx > 0
        self.props["CanPlay"] = self.idx < len(self.playlist)
        self.props["CanPause"] = self.props["PlaybackStatus"] != 'Stopped' and self.props["CanPlay"]
        self.props["CanSeek"] = self.props["CanPlay"] # for now
    
    def setPlaylist(self, playlist):
        with self.lock:
            self.logger.debug('setPlaylist')
            if self.props["PlaybackStatus"] != 'Stopped':
                self._stop()
                self.props["PlaybackStatus"] = 'Stopped'
            self.idx = 0
            self.playlist = playlist
            for item in playlist:
                self.MpYt.fileManager.fetchVideo(item["videoId"])
            self.updateProps()

    def play(self):
        with self.lock:
            self.logger.debug('play')
            if self.props["PlaybackStatus"] == 'Playing':
                self.logger.warning("Already running")
                return

            if self.props["PlaybackStatus"] == 'Paused':
                self._curPlayer.playing = True
            else:
                self._spawn()
            self.props["PlaybackStatus"] = 'Playing'
            self.updateProps()

    def pause(self):
        with self.lock:
            self.logger.debug('pause')
            if self.props["PlaybackStatus"] != 'Playing':
                self.logger.warning("Not playing")
            else:
                self.props["PlaybackStatus"] = 'Paused'
                self._curPlayer.playing = False
                self.updateProps()
    
    def stop(self):
        with self.lock:
            self.logger.debug('stop')
            if self.props["PlaybackStatus"] == 'Stopped':
                self.logger.warning("Already stopped")
            else:
                self._stop()
                self.idx = 0
                self.props["PlaybackStatus"] = 'Stopped'
                self.updateProps()

    def seek(self, offset):
        with self.lock:
            self.logger.debug('seek %f' % offset)
            newPos = self._curPlayer.wav.tell() + int(offset * self._curPlayer.wav.getframerate() / 1000)
            self._curPlayer.wav.setpos(min(max(0, newPos), self._curPlayer.wav.getnframes()))

    def next(self):
        with self.lock:
            self.logger.debug('next')
            if self.idx + 1 >= len(self.playlist):
                raise RuntimeError('No such song')
            self._stop()
            self.idx += 1
            self._spawn()
            self.updateProps()

    def prev(self):
        with self.lock:
            self.logger.debug('prev')
            if self.idx - 1 < 0:
                raise RuntimeError('No such song')
            self._stop()
            self.idx -= 1
            self._spawn()
            self.updateProps()

    def _spawn(self):
        self.logger.debug('_spawn')

        def finish():
            if self.props["CanGoNext"]:
                self.idx += 1
                self._spawn()
            else:
                self.props["PlaybackStatus"] = 'Stopped'
            self.updateProps()

        def update():
            self.props["Position"] = int(self._curPlayer.wav.tell() / self._curPlayer.wav.getframerate() * 1000)

        try:
            video = self.MpYt.fileManager.getVideo(self.playlist[self.idx]["videoId"])
            self.props["PlaybackStatus"] = 'Playing'
            self._curPlayer = Player._player(video, self.lock, update, finish)
        except:
            self.logger.warning("Something bad happened! Skipping this video instead.")
            finish()

    def _stop(self):
        self.logger.debug('_stop')
        self.props["Position"] = 0
        self._curPlayer.terminate = True

class MprisYoutube:

    def __init__(self):
        self.logger = Logger('MprisYoutube')

        self.player = Player(self)
        self.userInterface = UserInterface(self)
        self.dbusInterface = DBusInterface(self)
        self.fileManager = FileManager()

        self.props = dict(
                CanQuit=True,
                #FullScreen=False,
                #CanSetFullscreen,
                CanRaise=False,
                HasTrackList=True,
                Identity='mpris-youtube',
                #DesktopEntry='What is this?',
                SupportedUriSchemes=[], # can't open uri from outside
                SupportedMimeTypes=['application/x-flash-video'])

        """
        for playlist in self.getLists():
            print 'list %s:' % playlist["title"]

            for item in self.getItems(playlist["id"], False):
                print '\t%s (%s, %s)' % (item["title"], item["id"], item["videoId"])
                """
    def run(self):
        self.userInterface.start()
        self.userInterface.join()

    def getLists(self, part="id,snippet,contentDetails"):

        token = ""
        result = []
        youtube = APIService.instance(authenticate=True)

        while True:
            listResp = youtube.playlists().list(
                    part=part,
                    pageToken=token,
                    maxResults=50,
                    mine=True
                    ).execute()
            for item in listResp["items"]:
                element = dict(
                        id=item["id"],
                        title=item["snippet"]["title"],
                        description=item["snippet"]["description"],
                        itemCount=item["contentDetails"]["itemCount"])
                result.append(element)

            try:
                token = listResp["nextPageToken"]
            except:
                return result

    def getItems(self, playlistId, authenticate=True, part="id,snippet"):

        token = ""
        result = []
        youtube = APIService.instance(authenticate=authenticate)

        while True:
            itemResp = youtube.playlistItems().list(
                    part=part,
                    pageToken=token,
                    maxResults=50,
                    playlistId=playlistId
                    ).execute()
            for item in itemResp["items"]:
                element = dict(
                        id=item["id"],
                        title=item["snippet"]["title"],
                        videoId=item["snippet"]["resourceId"]["videoId"])
                result.append(element)

            try:
                token = itemResp["nextPageToken"]
            except:
                return result

def main():

    MpYt = MprisYoutube()
    MpYt.run()

    print 'Good bye :)'

if __name__ == "__main__":
    main()

