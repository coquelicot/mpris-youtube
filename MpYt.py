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
        config["runtimeDir"] = os.path.join(os.environ['HOME'], '.fcrh', 'mpris-youtube', 'var')
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

    @classmethod
    def _queryAll(cls, callback):

        token = ""
        result = []

        while True:
            resp = callback(token)
            result.extend(resp["items"])

            try:
                token = resp["nextPageToken"]
            except:
                return result

    @classmethod
    def getLists(cls):

        youtube = cls.instance(authenticate=True)
        def callback(token):
            return youtube.playlists().list(
                    part="id,snippet,contentDetails",
                    pageToken=token,
                    maxResults=50,
                    mine=True
                    ).execute()

        return cls._queryAll(callback)

    @classmethod
    def getList(cls, title=None, listId=None):

        if listId is not None:
            resp = cls.instance(authenticate=True).playlists().list(
                    part="id,snippet,contentDetails",
                    id=listId,
                    mine=True)
            if len(resp["items"] > 0):
                return resp["items"][0]

        elif title is not None:
            for item in cls.getLists():
                if item["snippet"]["title"] == title:
                    return item

        else:
            raise ValueError("Please give me title or listId QQ")

        return None

    @classmethod
    def getItems(cls, playlistId, authenticate=True):

        youtube = cls.instance(authenticate=authenticate)
        def callback(token):
            return youtube.playlistItems().list(
                    part="id,snippet",
                    pageToken=token,
                    maxResults=50,
                    playlistId=playlistId
                    ).execute()

        return cls._queryAll(callback)

class FileManager:

    EXTENTIONS = ['wav']
    DOWNLOAD_URI = 'http://www.youtube.com/watch?v=%s'

    fetchSet = set()
    lock = threading.Lock()
    fnull = open(os.devnull, 'w')

    class _fetcher(threading.Thread):

        idCnt = 0
        requests = Queue.Queue()
        cond = threading.Condition()

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
                code = subprocess.call(prog, stdout=FileManager.fnull, stderr=FileManager.fnull)

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
        while True:
            with FileManager.lock:
                if videoId not in FileManager.fetchSet:
                    raise RuntimeError("Video not in cache set.")

            for ext in FileManager.EXTENTIONS:
                path = os.path.join(config.storageDir, 'video', videoId + '.' + ext)
                if os.path.isfile(path):
                    return wave.open(path, 'rb')
            self.logger.info("Waiting for video to be fetch..")
            time.sleep(10)

    def loadSet(self):
        result = set()
        for fileName in os.listdir(os.path.join(config.storageDir, 'video')):
            videoId, ext = fileName.rsplit('.', 1)
            if ext in FileManager.EXTENTIONS:
                result.add(videoId)
        return result

class DBusInterface(dbus.service.Object):

    NAME = "org.mpris.MediaPlayer2.MpYt"
    PATH = "/org/mpris/MediaPlayer2"
    IFACE_MAIN = "org.mpris.MediaPlayer2"
    IFACE_PLAYER = "org.mpris.MediaPlayer2.Player"
    #IFACE_PLAYLIST = "org.mpris.MediaPlayer2.Playlists"
    IFACE_PROPERTY = "org.freedesktop.DBus.Properties"

    def __init__ (self, MpYt):
        self.MpYt = MpYt
        self.logger = Logger('DBusInterface')

        self.bus = dbus.SessionBus()
        busName = dbus.service.BusName(DBusInterface.NAME, bus=self.bus)
        dbus.service.Object.__init__(self, busName)
        self.add_to_connection(self.bus, DBusInterface.PATH)

    # org.mpris.MediaPlayer2
    @dbus.service.method(IFACE_MAIN)
    def Raise(self):
        raise RuntimeError("Don't have a gui yet.")

    @dbus.service.method(IFACE_MAIN)
    def Quit(self):
        self.logger.info('Quit')
        self.MpYt.loop.quit()

    # org.mpris.MediaPlayer2.Player
    @dbus.service.method(IFACE_PLAYER)
    def Next(self):
        if self.MpYt.player.props['CanGoNext']:
            self.MpYt.player.next()

    @dbus.service.method(IFACE_PLAYER)
    def Previous(self):
        if self.MpYt.player.props['CanGoPrevious']:
            self.MpYt.player.prev()

    @dbus.service.method(IFACE_PLAYER)
    def Pause(self):
        if self.MpYt.player.props['CanPause']:
            self.MpYt.player.pause()

    @dbus.service.method(IFACE_PLAYER)
    def PlayPause(self):
        if self.MpYt.player.props['CanPlayPause']:
            if self.MpYt.player.props['PlaybackStatus'] == 'Paused':
                self.MpYt.player.play()
            else:
                self.MpYt.player.pause()
        else:
            raise RuntimeError('Error')

    @dbus.service.method(IFACE_PLAYER)
    def Stop(self):
        if self.MpYt.player.props['CanControl']:
            self.MpYt.player.stop()
        else:
            raise RuntimeError('Error')

    @dbus.service.method(IFACE_PLAYER)
    def Play(self):
        if self.MpYt.player.props['CanPlay']:
            self.MpYt.player.play()

    @dbus.service.method(IFACE_PLAYER, in_signature='x')
    def Seek(self, offset):
        if self.MpYt.player.props['CanSeek']:
            self.MpYt.player.seek(offset)

    @dbus.service.method(IFACE_PLAYER, in_signature='ox')
    def SetPosition(self, trackId, position):
        if self.MpYt.player.props['CanSeek']:
            if self.MpYt.player.props['Metadata']['mpris:trackid'] != trackId:
                self.logger.warning("Stale request of SetPosition.")
            else:
                self.MpYt.player.setPos(position)

    @dbus.service.method(IFACE_PLAYER, in_signature='s')
    def OpenUri(self, uri):
        raise RuntimeError('Error')

    @dbus.service.signal(IFACE_PLAYER, signature='x')
    def Seeked(self, position):
        self.logger.debug('Seeked: %d (%d)' % (position, self.MpYt.player.props["Position"]))

    # org.freedesktop.DBus.Properties
    @dbus.service.method(IFACE_PROPERTY, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        if interface == DBusInterface.IFACE_MAIN:
            return self.MpYt.props
        elif interface == DBusInterface.IFACE_PLAYER:
            return self.MpYt.player.props
        else:
            raise RuntimeError("Key Error")

    @dbus.service.method(IFACE_PROPERTY, in_signature='ss', out_signature='v')
    def Get(self, interface, prop):
        if interface == DBusInterface.IFACE_MAIN:
            return self.MpYt.props[prop]
        elif interface == DBusInterface.IFACE_PLAYER:
            return self.MpYt.player.props[prop]
        else:
            raise RuntimeError("Key Error")

    @dbus.service.method(IFACE_PROPERTY, in_signature='ssv')
    def Set(self, interface, prop, value):
        # FIXME: shoudn't pass
        pass

    @dbus.service.signal(IFACE_PROPERTY, signature='sa{sv}as')
    def PropertiesChanged(self, interface_name, changed_properties, invalidated_properties):
        self.logger.debug('PropChange: %s, %s'  % (interface_name, repr(changed_properties)))

class UserInterface(threading.Thread):

    def __init__(self, MpYt):
        threading.Thread.__init__(self)
        self.daemon = True

        self.MpYt = MpYt

    def run(self):

        while True:
            cmd = raw_input('>> ').split()
            if cmd[0] == 'playlist.list':
                print ', '.join([item["snippet"]["title"] for item in APIService.getLists()])
            elif cmd[0] == 'playlist.play':
                objList = APIService.getList(title=cmd[1])
                self.MpYt.player.setPlaylist(APIService.getItems(objList["id"], authenticate=False), objList)
                self.MpYt.player.play()
            elif cmd[0] == 'playlistItem.list':
                listId = APIService.getList(title=cmd[1])["id"]
                print ', '.join([item["snippet"]["title"] for item in APIService.getItems(listId, authenticate=False)])
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
                self.MpYt.loop.quit()

class Playlist:

    PlaylistCnt = 0

    class Item:

        ItemCnt = 0

        def __init__(self, data):
            Playlist.Item.ItemCnt += 1
            self.idCnt = Playlist.Item.ItemCnt

            self.id = data["id"]
            self.title = data["snippet"]["title"]
            self.position = data["snippet"]["position"]
            self.description = data["snippet"]["description"]
            self.videoId = data["snippet"]["resourceId"]["videoId"]
            self.thumbnail = data["snippet"]["thumbnails"]["default"]

    def __init__(self, title=None, listId=None, data=None, fetchItem=False):
        Playlist.PlaylistCnt += 1
        self.idCnt = Playlist.PlaylistCnt
        self.logger = Logger('Playlist%d' % self.idCnt)

        if data is None:
            data = APIService.getList(title=title, listId=listId)
        self.id = data["id"]
        self.title = data["snippet"]["title"]
        self.count = data["contentDetial"]["itemCount"]

        self.fetchItem = fetchItem
        if fetchItem:
            self.audios = [Playlist.Item(item) for item in APIService.getItems(self.id)]

class Player:

    audio = pyaudio.PyAudio()

    class _player(threading.Thread):

        def __init__ (self, lock, update, finish):
            threading.Thread.__init__(self)
            self.daemon = True

            self.wav = None
            self.update = update
            self.finish = finish
            self.stream = None
            self.cond = threading.Condition(lock)

            self.start()

        def playWave(self, wav):
            if self.stream is not None:
                self.stream.close()

            self.wav = wav
            self.stream = Player.audio.open(
                    format=Player.audio.get_format_from_width(wav.getsampwidth()),
                    channels=wav.getnchannels(),
                    rate=wav.getframerate(),
                    output=True)
            self.cond.notify()

        def pause(self):
            self.stream.stop_stream()

        def resume(self):
            self.stream.start_stream()
            self.cond.notify()

        def seek(self, offset):
            newPos = self.wav.tell() + int(offset * self.wav.getframerate() / 1000)
            self.wav.setpos(min(max(0, newPos), self.wav.getnframes()))

        def setPos(self, pos):
            newPos = int(pos * self.wav.getframerate() / 1000)
            if newPos >= 0 and newPos <= self.wav.getnframes():
                self.wav.setpos(newPos)

        def getPos(self):
            return int(self.wav.tell() * 1000 / self.wav.getframerate())

        def run(self):
            while True:

                self.cond.acquire()
                while self.stream is None or self.stream.is_stopped():
                    self.cond.wait()

                data = self.wav.readframes(1024)
                if data == '':
                    self.stream.close()
                    self.stream = None
                    self.finish()
                else:
                    self.stream.write(data)
                    self.update()
                self.cond.release()

    def __init__(self, MpYt):
        self.MpYt = MpYt
        self.idx = 0
        self.playlist = []
        self.playlistInfo = None
        self.lock = threading.Lock()
        self.logger = Logger('Player')

        self.props = dict(
                PlaybackStatus='Stopped', # Playing, Paused, Stopped
                #LoopStatusk='None', # None, Track, Playlist
                Rate=1.0, # only 1.0 for now
                #Shuffle=False,
                Metadata=dbus.Dictionary(signature='sv'), # FIXME: should contains something
                Volume=0.5,
                Position=0L,
                MinimumRate=0.5,
                MaximumRate=0.5,
                CanGoNext=False,
                CanGoPrevious=False,
                CanPlay=False,
                CanPause=False,
                CanPlayPause=False,
                CanSeek=True,
                CanControl=True)
        self._copyProps = self.props.copy()

        self._player = Player._player(self.lock, self.updateCallback, self.finishCallback)

    def updateProps(self):
        self.logger.debug('updateProps')
        self.props["CanGoNext"] = self.idx < len(self.playlist) - 1
        self.props["CanGoPrevious"] = self.idx > 0
        self.props["CanPlay"] = self.idx < len(self.playlist)
        self.props["CanPause"] = self.props["PlaybackStatus"] != 'Stopped' and self.props["CanPlay"]
        self.props["CanPlayPause"] = self.props["CanPlay"]
        self.props["CanSeek"] = True # for now

        changeDict = dict()
        for key, value in self.props.items():
            if value != self._copyProps[key]:
                changeDict[key] = value
                self._copyProps[key] = value
        self.MpYt.dbusInterface.PropertiesChanged(DBusInterface.IFACE_PLAYER, changeDict, dbus.Array(signature='s'))
    
    def setPlaylist(self, playlist, playlistInfo=None):
        with self.lock:
            self.logger.debug('setPlaylist')
            if self.props["PlaybackStatus"] != 'Stopped':
                self._stop()
                self.props["PlaybackStatus"] = 'Stopped'
                self.updateProps()
            self.idx = 0
            self.playlist = playlist
            self.playlistInfo = playlistInfo
            for item in playlist:
                self.MpYt.fileManager.fetchVideo(item["snippet"]["resourceId"]["videoId"])
            self.updateProps()

    def play(self):
        with self.lock:
            self.logger.debug('play')
            if self.props["PlaybackStatus"] == 'Playing':
                self.logger.warning("Already running")
                return

            if self.props["PlaybackStatus"] == 'Paused':
                self._player.resume()
                self.props["PlaybackStatus"] = 'Playing'
            else:
                self._spawn()
            self.updateProps()

    def pause(self):
        with self.lock:
            self.logger.debug('pause')
            if self.props["PlaybackStatus"] != 'Playing':
                self.logger.warning("Not playing")
            else:
                self.props["PlaybackStatus"] = 'Paused'
                self._player.pause()
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
            if self.props["CanSeek"]:
                self.logger.debug('seek %d' % offset)
                self._player.seek(offset)
                self.updateCallback() # FIXME: not so appropriate
                self.MpYt.dbusInterface.Seeked(dbus.Int64(self._player.getPos()))
            else:
                raise RuntimeError("Can't seek")

    def setPos(self, pos):
        with self.lock:
            if self.props["CanSeek"]:
                self.logger.debug('setPos %d' % pos)
                self._player.setPos(pos)
                self.updateCallback() # FIXME: not so appropriate
                self.MpYt.dbusInterface.Seeked(dbus.Int64(self._player.getPos()))
            else:
                raise RuntimeError("Can't setPos")

    def next(self):
        with self.lock:
            self.logger.debug('next')
            if self.idx + 1 >= len(self.playlist):
                raise RuntimeError('No such song')
            self.idx += 1
            self._spawn()
            self.updateProps()

    def prev(self):
        with self.lock:
            self.logger.debug('prev')
            if self.idx - 1 < 0:
                raise RuntimeError('No such song')
            self.idx -= 1
            self._spawn()
            self.updateProps()

    def finishCallback(self):
        self.idx += 1
        if self.props["CanGoNext"]:
            self._spawn()
        else:
            self.props["PlaybackStatus"] = 'Stopped'
        self.updateProps()

    def updateCallback(self):
        self.props["Position"] = long(self._player.getPos())

    def _spawn(self):
        self.logger.debug('_spawn')

        try:
            videoId = self.playlist[self.idx]["snippet"]["resourceId"]["videoId"]
            video = self.MpYt.fileManager.getVideo(videoId)
            youtube = APIService.instance(authenticate=False)
            videoInfo = youtube.videos().list(id=videoId, part="snippet").execute()["items"][0]

            self.props["Metadata"] = {
                    "mpris:trackid": dbus.ObjectPath(DBusInterface.PATH + '/video/' + videoId, variant_level=1),
                    "mpris:artUrl": dbus.UTF8String(videoInfo["snippet"]["thumbnails"]["default"]["url"].encode('utf-8'), variant_level=1),
                    "mpris:length": dbus.Int64(video.getnframes() / video.getframerate() * 1000, variant_level=1),
                    "xesam:title": dbus.UTF8String(videoInfo["snippet"]["title"].encode('utf-8'), variant_level=1),
                    }
            if self.playlistInfo is not None:
                # using playlist's title instread
                self.props["Metadata"]["xesam:album"] = dbus.UTF8String(self.playlistInfo["snippet"]["title"].encode('utf-8'), variant_level=1)
            self.props["Position"] = 0L
            self.props["PlaybackStatus"] = 'Playing'
            self._player.playWave(video)
        except:
            self.logger.warning("Something bad happened! Skipping this video instead.")
            self.finishCallback()

    def _stop(self):
        self.logger.debug('_stop')
        self.props["Position"] = 0L
        self._player.pause()

class MprisYoutube:

    def __init__(self):
        self.logger = Logger('MprisYoutube')
        gobject.threads_init()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.loop = gobject.MainLoop()

        self.player = Player(self)
        self.userInterface = UserInterface(self)
        self.dbusInterface = DBusInterface(self)
        self.fileManager = FileManager()

        self.props = dict(
                CanQuit=True,
                #FullScreen=False,
                #CanSetFullscreen,
                CanRaise=False,
                HasTrackList=False,
                Identity='mpris-youtube',
                #DesktopEntry='What is this?',
                SupportedUriSchemes=dbus.Array(signature='s'), # can't open uri from outside
                SupportedMimeTypes=['audio/wav'])

    def run(self):
        self.userInterface.start()
        try:
            self.loop.run()
        except:
            self.loop.quit()

if __name__ == "__main__":
    MprisYoutube().run()
    print 'Good bye :)'

