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
import time
import datetime
import threading
import subprocess
import httplib2

import mad
import wave
import pyaudio
import audioop

from apiclient.discovery import build
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.tools import run
from oauth2client.keyring_storage import Storage

import gobject
import dbus
import dbus.service
import dbus.mainloop.glib

class Config:

    CONFIGFILE = os.path.join(os.environ['HOME'], '.fcrh', '.mpris-youtube', 'conf.txt')

    def __init__(self):

        # setup default configure
        config = dict()
        config["storageDir"] = os.path.join(os.environ['HOME'], '.fcrh', 'mpris-youtube', 'data')
        config["runtimeDir"] = os.path.join(os.environ['HOME'], '.fcrh', 'mpris-youtube', 'var')
        config["fetchThreads"] = 2

        try:
            with open(Config.CONFIGFILE, 'r') as fin:
                for line in fin.readlines():
                    key, value = line.split('=', 1)
                    config[key.strip()] = self.autoConvertType(value.strip())
            print 'Config loaded.'
        except:
            print "Can't load config file `%s', using default config." % Config.CONFIGFILE

        self.__dict__ = config

    def autoConvertType(self, value):
        if value.isdigit():
            return int(value)
        else:
            return value

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
                    part="id,snippet",
                    pageToken=token,
                    maxResults=50,
                    mine=True
                    ).execute()

        return cls._queryAll(callback)

    @classmethod
    def getList(cls, title=None, listId=None):

        if listId is not None:
            resp = cls.instance(authenticate=True).playlists().list(
                    part="id,snippet",
                    id=listId,
                    ).execute()
            if len(resp["items"]) > 0:
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

    @classmethod
    def getVideo(cls, videoId, authenticate=True):
        youtube = cls.instance(authenticate=authenticate)
        return youtube.videos().list(
                part="id,snippet",
                id=videoId
                ).execute()['items'][0]

    @classmethod
    def insertItem(cls, playlistId, videoId, position=None):

        snippet = dict(playlistId=playlistId, resourceId=dict(kind="youtube#video", videoId=videoId))
        if position:
            snippet["position"] = position

        cls.instance(authenticate=True).playlistItems().insert(
                part='snippet',
                body=dict(snippet=snippet)
                ).execute()


class FileManager:

    ONLINE_EXT = 'online'
    EXTENTIONS = ['mp3', 'wav']
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
                    '-x', '--audio-format', 'mp3']
                code = subprocess.call(prog, stdout=FileManager.fnull, stderr=FileManager.fnull, close_fds=True)

                if code == 0:
                    self.logger.info("video %s fetched" % self.videoId)
                else:
                    if code < 0:
                        self.logger.warning('Youtube-dl killed by signal %d' % -code)
                    elif code > 0:
                        self.logger.warning("Youtube-dl doesn't return 0!!")
                    with FileManager.lock:
                        FileManager.fetchSet.remove(self.videoId)

    class _video:

        VideoCnt = 0

        def __init__(self, path, fileType):
            FileManager._video.VideoCnt += 1
            self.idx = FileManager._video.VideoCnt
            self.logger = Logger('_video%d' % self.idx)
            self.fileType = fileType
            self.closed = False

            if fileType == FileManager.ONLINE_EXT: # path = download path
                self.logger.info("Init from `%s'" % path)
                self.canSeek = False

                dlPath = os.path.join(config.runtimeDir, 'dlFifo')
                cvPath = os.path.join(config.runtimeDir, 'cvFifo.mp3') # convert to mp3

                if os.path.exists(dlPath):
                    os.remove(dlPath)
                if os.path.exists(cvPath):
                    os.remove(cvPath)
                os.mkfifo(dlPath)
                os.mkfifo(cvPath)

                dlProg = ['youtube-dl', '--quiet', '--max-quality', '43', '--prefer-free-formats', path, '-o', dlPath]
                cvProg = ['avconv', '-y', '-i', dlPath, cvPath]
                self.dlChild = subprocess.Popen(dlProg, stderr=FileManager.fnull, stdout=FileManager.fnull)
                self.cvChild = subprocess.Popen(cvProg, stderr=FileManager.fnull, stdout=FileManager.fnull)

                self.video = mad.MadFile(cvPath)
                self.getsampwidth = lambda: 2 # verify me
                self.getnchannels = lambda: 1 if self.video.mode == mad.MODE_SINGLE_CHANNEL else 2 # verify me!
                self.getnframes = lambda: 0
                self.getframerate = self.video.samplerate
                self.read = self.video.read
                self.tell = lambda: int(self.video.current_time() * self.video.samplerate())
                #self.setPos = lambda pos: self.video.seek_time(pos / self.video.samplerate() * 1000)

            else: # path = local file path
                self.canSeek = True

                if fileType == 'wav':
                    self.logger.info("Init with wav file")

                elif fileType == 'mp3':
                    self.logger.info("Init with mp3 file (convert to wav)")

                    wavePath = os.path.join(config.runtimeDir, '.tmp.wav')
                    code = subprocess.call(['avconv', '-y', '-i', path, wavePath], stdout=FileManager.fnull, stderr=FileManager.fnull)
                    if code < 0:
                        raise RuntimeError("Can't convert file `%s'" % path)
                    else:
                        path = wavePath

                else:
                    raise ValueError('What is this?')

                self.video = wave.open(path, 'rb')
                self.getsampwidth = self.video.getsampwidth
                self.getnchannels = self.video.getnchannels
                self.getnframes = self.video.getnframes
                self.getframerate = self.video.getframerate
                self.read = lambda: self.video.readframes(1024)
                self.tell = self.video.tell
                self.setPos = self.video.setpos

        def close(self):
            if not self.closed:
                self.closed = True
                try:
                    if self.fileType == FileManager.ONLINE_EXT:
                        self.logger.debug('remove online stream')
                        if not self.dlChild.poll():
                            self.dlChild.kill()
                            self.dlChild.wait()
                        if not self.cvChild.poll():
                            self.cvChild.kill()
                            self.cvChild.wait()
                    else:
                        self.video.close()
                except:
                    pass

    def __init__(self):
        self.logger = Logger('FileManager')
        FileManager.fetchSet = self.loadSet()
        while FileManager._fetcher.idCnt < config.fetchThreads:
            FileManager._fetcher().start()

    def fetchVideo(self, videoId):
        with FileManager.lock:
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

        for ext in FileManager.EXTENTIONS:
            path = os.path.join(config.storageDir, 'video', videoId + '.' + ext)
            if os.path.isfile(path):
                return FileManager._video(path, ext)
        return FileManager._video(FileManager.DOWNLOAD_URI % videoId, FileManager.ONLINE_EXT)

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
    IFACE_PLAYLISTS = "org.mpris.MediaPlayer2.Playlists"
    IFACE_TRACKLIST = "org.mpris.MediaPlayer2.TrackList"
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

    # org.mpris.MediaPlayer2.Playlists
    @dbus.service.method(IFACE_PLAYLISTS, in_signature='o')
    def ActivatePlaylist(self, playlistId):
        self.MpYt.player.setPlaylist(Playlist.getList(listId=Playlist.pathToId(playlistId)))

    @dbus.service.method(IFACE_PLAYLISTS, in_signature='uusb', out_signature='a(oss)')
    def GetPlaylists(self, index, maxCount, order, reverse):
        if order == 'Alphabetical':
            lists = sorted(Playlist.getLists(), key=lambda obj: obj.title)
        else:
            raise ValueError('Does not support this order')
        return dbus.Array([item.mprisFormat() for item in lists[index:index+maxCount]])

    @dbus.service.signal(IFACE_PLAYLISTS, signature='(oss)')
    def PlaylistChanged(self, playlist):
        self.logger.info('Playlist changed: %s' % repr(playlist))

    # org.mpris.MediaPlayer2.TrackList
    @dbus.service.method(IFACE_TRACKLIST, in_signature='ao', out_signature='aa{sv}')
    def GetTracksMetadata(self, tracks):
        raise NotImplementedError('GetTracksMetadata')

    @dbus.service.method(IFACE_TRACKLIST, in_signature='sob')
    def AddTrack(self, url, afterTrack, setAsCurrent):
        if self.MpYt.player.trackProps['CanEditTracks']:
            raise NotImplementedError('AddTrack (should not happend)')

    @dbus.service.method(IFACE_TRACKLIST, in_signature='o')
    def RemoveTrack(self, trackId):
        if self.MpYt.player.trackProps['CanEditTracks']:
            raise NotImplementedError('RemoveTrack (should not happend)')

    @dbus.service.method(IFACE_TRACKLIST, in_signature='o')
    def GoTo(self, trackId):
        self.MpYt.player.jump(int(trackId.rsplit('/', 1)[1]))

    @dbus.service.signal(IFACE_TRACKLIST, signature='aoo')
    def TrackListReplaced(self, tracks, currentTrack):
        self.logger.debug('TraceListReplaced: %s %s' % (repr(tracks), repr(currentTrack)))

    @dbus.service.signal(IFACE_TRACKLIST, signature='a{sv}o')
    def TrackAdded(self, metadata, afterTrack):
        self.logger.debug('TrackAdd: %s %s' % (repr(metadata), repr(afterTrack)))

    @dbus.service.signal(IFACE_TRACKLIST, signature='o')
    def TrackRemoved(self, trackId):
        self.logger.debug('TrackRemoved: %s' % repr(trackId))

    @dbus.service.signal(IFACE_TRACKLIST, signature='oa{sv}')
    def TrackMetadataChanged(self, trackId, metadata):
        self.logger.debug('TrackMetadataChanged: %s %s' % (repr(trackId), repr(metadata)))

    # org.freedesktop.DBus.Properties
    @dbus.service.method(IFACE_PROPERTY, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        if interface == DBusInterface.IFACE_MAIN:
            return self.MpYt.props
        elif interface == DBusInterface.IFACE_PLAYER:
            return self.MpYt.player.props
        elif interface == DBusInterface.IFACE_PLAYLISTS:
            return Playlist.props
        elif interface == DBusInterface.IFACE_TRACKLIST:
            return self.MpYt.player.trackProps
        else:
            raise ValueError('No such interface')

    @dbus.service.method(IFACE_PROPERTY, in_signature='ss', out_signature='v')
    def Get(self, interface, prop):
        if interface == DBusInterface.IFACE_MAIN:
            return self.MpYt.props[prop]
        elif interface == DBusInterface.IFACE_PLAYER:
            return self.MpYt.player.props[prop]
        elif interface == DBusInterface.IFACE_PLAYLISTS:
            return Playlist.props[prop]
        elif interface == DBusInterface.IFACE_TRACKLIST:
            return self.MpYt.player.trackProps[prop]
        else:
            raise ValueError('No such interface')

    @dbus.service.method(IFACE_PROPERTY, in_signature='ssv')
    def Set(self, interface, prop, value):
        if interface == DBusInterface.IFACE_PLAYER:
            if self.MpYt.player.props['CanControl']:
                if prop == 'Volume':
                    self.MpYt.player.setVolume(value)
                if prop == 'LoopStatus':
                    self.MpYt.player.setLoop(value)

            # We may ignore the setting of 'Rate' since its max & min are both 1.0

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
                print ', '.join([item.title for item in Playlist.getLists(fetchItem=False)])
            elif cmd[0] == 'playlist.play':
                self.MpYt.player.setPlaylist(Playlist.getList(title=cmd[1], fetchItem=True))
            elif cmd[0] == 'playlistItem.list':
                songList = Playlist.getList(title=cmd[1], fetchItem=True).audios
                for i in range(0, len(songList)):
                    print i + 1, songList[i].title, '(' + songList[i].id + ')'
            elif cmd[0] == 'playlistItem.insert':
                Playlist.getList(cmd[1]).addItem(cmd[2] if len(cmd) > 2 else Playlist.LOCAL_ID)
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
            elif cmd[0] == 'current.jump':
                self.MpYt.player.jump(int(cmd[1])-1)
            elif cmd[0] == 'config.setLoop':
                self.MpYt.player.setLoop(cmd[1])
            elif cmd[0] == 'exit':
                self.MpYt.loop.quit()

class Playlist:

    PlaylistCnt = 0
    props = {
        'PlaylistCount': dbus.UInt32(len(APIService.getLists())),
        'Orderings': dbus.Array(['Alphabetical']),
        'ActivePlaylist': dbus.Struct(
            (dbus.Boolean(False), dbus.Struct((dbus.ObjectPath('/'), '', ''))),
            signature="(b(oss))"),
    }

    LOCAL_ID = '_local'

    class Item:

        def __init__(self, data, isPlaylistItem=True):
            # accept both video and playlistItem
            self.id = data["snippet"]["resourceId"]["videoId"] if isPlaylistItem else data["id"]
            self.title = data["snippet"]["title"]
            self.thumbnail = data["snippet"]["thumbnails"]["default"]

    def __init__(self, title=None, listId=None, data=None, fetchItem=False):
        Playlist.PlaylistCnt += 1
        self.idCnt = Playlist.PlaylistCnt
        self.logger = Logger('Playlist%d' % self.idCnt)

        if data is None:
            data = APIService.getList(title=title, listId=listId)
        self.id = data["id"]
        self.title = data["snippet"]["title"]

        self.fetchItem = fetchItem
        if fetchItem:
            self.audios = [Playlist.Item(item) for item in APIService.getItems(self.id)]

    def addItem(self, videoId=None, data=None):

        if data is None:
            if videoId:
                data = Playlist.Item(APIService.getVideo(videoId), isPlaylistItem=False)
            else:
                return ValueError('must provide videoId or data')
        elif not isinstance(data, Playlist.Item):
            raise TypeError('data must be a instance of Playlist.Item')

        self.logger.info('add item ' + data.id)
        self.audios.append(data)

        if self.id != Playlist.LOCAL_ID:
            self.logger.info('update to youtube')
            APIService.insertItem(self.id, data.id)

    def mprisFormat(self):
        return dbus.Struct((self.dbusPath(), self.title, ''), signature="(oss)")

    def dbusPath(self):
        return dbus.ObjectPath(DBusInterface.PATH + '/playlist/' + Playlist._encode(self.id))

    @classmethod
    def pathToId(cls, path):
        return Playlist._decode(path.rsplit('/', 1)[1])

    @classmethod
    def _encode(cls, string):
        return '_'.join(map(str, map(ord, string)))

    @classmethod
    def _decode(cls, string):
        return ''.join(map(chr, map(int, string.split('_'))))

    @classmethod
    def getLists(cls, fetchItem=False):
        return [Playlist(data=item, fetchItem=fetchItem) for item in APIService.getLists()]

    @classmethod
    def getList(cls, title=None, listId=None, fetchItem=True):
        if title == Playlist.LOCAL_ID:
            return Playlist.localList
        else:
            return Playlist(data=APIService.getList(title=title, listId=listId), fetchItem=fetchItem)

Playlist.localList = Playlist(data=dict(id=Playlist.LOCAL_ID, snippet=dict(title=Playlist.LOCAL_ID)))

class Player:

    audio = pyaudio.PyAudio()

    class _player(threading.Thread):

        def __init__ (self, lock, update, finish, process):
            threading.Thread.__init__(self)
            self.daemon = True

            self.video = None
            self.update = update
            self.finish = finish
            self.process = process
            self.stream = None
            self.cond = threading.Condition(lock)

            self.start()

        def playAudio(self, video):
            if self.stream is not None:
                self.stream.close()

            self.video = video
            self.stream = Player.audio.open(
                    format=Player.audio.get_format_from_width(video.getsampwidth()),
                    channels=video.getnchannels(),
                    rate=video.getframerate(),
                    output=True)
            self.cond.notify()

        def pause(self):
            self.stream.stop_stream()

        def resume(self):
            self.stream.start_stream()
            self.cond.notify()

        def seek(self, offset):
            newPos = self.video.tell() + int(offset * self.video.getframerate() / 1000000)
            self.video.setPos(min(max(0, newPos), self.video.getnframes()))

        def setPos(self, pos):
            newPos = int(pos * self.video.getframerate() / 1000000)
            if newPos >= 0 and newPos <= self.video.getnframes():
                self.video.setPos(newPos)

        def getPos(self):
            return int(self.video.tell() * 1000000 / self.video.getframerate())

        def canSeek(self):
            return self.stream is not None and self.video.canSeek

        def stop(self):
            self.stream.close()
            self.video.close()
            self.stream = None

        def run(self):
            while True:

                self.cond.acquire()
                while self.stream is None or self.stream.is_stopped():
                    self.cond.wait()

                data = self.video.read()
                if data:
                    self.stream.write(self.process(data))
                    self.update()
                else:
                    self.stop()
                    self.finish()
                self.cond.release()
                time.sleep(0.001)

    def __init__(self, MpYt):
        self.MpYt = MpYt
        self.idx = 0
        self.playlist = []
        self.playlistInfo = None
        self.lock = threading.Lock()
        self.logger = Logger('Player')

        self.props = dict(
                PlaybackStatus='Stopped', # Playing, Paused, Stopped
                LoopStatus='Playlist', # None, Track, Playlist
                Rate=1.0, # only 1.0 for now
                #Shuffle=False,
                Metadata=dbus.Dictionary(signature='sv'),
                Volume=0.5,
                Position=0L,
                MinimumRate=1.0,
                MaximumRate=1.0,
                CanGoNext=False,
                CanGoPrevious=False,
                CanPlay=False,
                CanPause=False,
                CanPlayPause=False,
                CanSeek=False,
                CanControl=False)
        self._copyProps = self.props.copy()

        self.trackProps = dict(
                Tracks=dbus.Array([], signature='o'),
                CanEditTracks=False)

        self._player = Player._player(self.lock, self.updateCallback, self.finishCallback, self.processCallback)

    def updateProps(self):
        self.logger.debug('updateProps')

        if self.props['LoopStatus'] == 'Playlist':
            self.props["CanGoNext"] = len(self.playlist) > 0
            self.props["CanGoPrevious"] = self.props["CanGoNext"]
            self.props["CanPlay"] = self.props["CanGoNext"]
        else:
            self.props["CanGoNext"] = self.idx < len(self.playlist) - 1
            self.props["CanGoPrevious"] = self.idx > 0
            self.props["CanPlay"] = self.idx < len(self.playlist)
        self.props["CanPause"] = self.props["PlaybackStatus"] != 'Stopped' and self.props["CanPlay"]
        self.props["CanPlayPause"] = self.props["CanPlay"]
        self.props["CanSeek"] = self._player.canSeek()

        changeDict = dict()
        for key, value in self.props.items():
            if value != self._copyProps[key]:
                changeDict[key] = value
                self._copyProps[key] = value
        if changeDict:
            self.MpYt.dbusInterface.PropertiesChanged(DBusInterface.IFACE_PLAYER, changeDict, dbus.Array(signature='s'))
        else:
            self.logger.debug('Nothing to update.')
    
    def setPlaylist(self, playlist, autoPlay=True):
        with self.lock:
            self.logger.debug('setPlaylist')

            if self.props["PlaybackStatus"] != 'Stopped':
                self._stop()
                self.props["PlaybackStatus"] = 'Stopped'
                # is this necessary?
                #self.updateProps()

            self.idx = 0
            self.playlist = playlist.audios
            self.playlistInfo = playlist
            for item in self.playlist:
                self.MpYt.fileManager.fetchVideo(item.id)
            self.updateProps()
            Playlist.props['ActivePlaylist'] = dbus.Struct((dbus.Boolean(True), playlist.mprisFormat()))

            self.MpYt.dbusInterface.TrackListReplaced(
                    dbus.Array([dbus.ObjectPath(DBusInterface.PATH + '/video/' + str(i)) for i in range(len(self.playlist))]),
                    dbus.ObjectPath(DBusInterface.PATH + '/video/0'))

        if autoPlay:
            self.play()

    def setVolume(self, value):
        with self.lock:
            value = min(1, max(0, value))
            self.props['Volume'] = value
            self.MpYt.dbusInterface.PropertiesChanged(DBusInterface.IFACE_PLAYER, {'Volume': dbus.Double(value)}, dbus.Array(signature='s'))

    def setLoop(self, value):
        with self.lock:
            if value in ['None', 'Track', 'Playlist']:
                self.props['LoopStatus'] = value
                self.MpYt.dbusInterface.PropertiesChanged(DBusInterface.IFACE_PLAYER, {'LoopStatus': value}, dbus.Array(signature='s'))
                self.updateProps()
            else:
                raise ValueError('Unknown loop state')

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

    def jump(self, idx):
        with self.lock:
            self.logger.debug('jump')
            if idx < 0 or idx >= len(self.playlist):
                self.logger.error('Invalid idx')
                return

            if self.props["PlaybackStatus"] != 'Stopped':
                self._stop()
            self.idx = idx
            self._spawn()
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

            if not self.props['CanGoNext']:
                raise RuntimeError('No such song')
            if self.props['PlaybackStatus'] != 'Stopped':
                self._stop()

            self.idx += 1
            if self.idx >= len(self.playlist):
                self.idx = 0
            self._spawn()
            self.updateProps()

    def prev(self):
        with self.lock:
            self.logger.debug('prev')

            if not self.props['CanGoPrevious']:
                raise RuntimeError('No such song')
            if self.props['PlaybackStatus'] != 'Stopped':
                self._stop()

            self.idx -= 1
            if self.idx < 0:
                self.idx = len(self.playlist) - 1
            self._spawn()
            self.updateProps()

    def finishCallback(self):

        if self.props['LoopStatus'] != 'Track':
            self.idx += 1
            if self.props['LoopStatus'] == 'Playlist' and self.idx >= len(self.playlist):
                self.idx = 0
        self.updateProps()

        if self.props["CanPlay"]:
            self._spawn()
        else:
            self.props["PlaybackStatus"] = 'Stopped'
        self.updateProps()

    def updateCallback(self):
        self.props["Position"] = long(self._player.getPos())

    def processCallback(self, data):
        # XXX: Not so appropriate?
        return audioop.mul(data, self._player.video.getsampwidth(), self.props["Volume"])

    def _spawn(self):
        self.logger.debug('_spawn')

        try:
            videoId = self.playlist[self.idx].id
            video = self.MpYt.fileManager.getVideo(videoId)
            youtube = APIService.instance(authenticate=False)
            videoInfo = youtube.videos().list(id=videoId, part="snippet").execute()["items"][0]

            self.props["Metadata"] = {
                    "mpris:trackid": dbus.ObjectPath(DBusInterface.PATH + '/video/' + str(self.idx), variant_level=1),
                    "mpris:artUrl": dbus.UTF8String(videoInfo["snippet"]["thumbnails"]["default"]["url"].encode('utf-8'), variant_level=1),
                    "xesam:title": dbus.UTF8String(videoInfo["snippet"]["title"].encode('utf-8'), variant_level=1),
                    # using playlist's title instread
                    "xesam:album": dbus.UTF8String(self.playlistInfo.title.encode('utf-8'), variant_level=1)
                    }
            # XXX: not so appropriate
            if video.canSeek:
                self.props["Metadata"]["mpris:length"] = dbus.Int64(video.getnframes() / video.getframerate() * 1000000, variant_level=1)
            self.props["Position"] = 0L
            self.props["PlaybackStatus"] = 'Playing'
            self._player.playAudio(video)
        except:
            self.logger.warning("Something bad happened! Skipping this video instead.")
            self.finishCallback()

    def _stop(self):
        self.logger.debug('_stop')
        self.props["Position"] = 0L
        self._player.stop()

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
                SupportedMimeTypes=['audio/wav', 'audio/mpeg'])

    def run(self):
        self.userInterface.start()
        try:
            self.loop.run()
        except:
            self.loop.quit()

if __name__ == "__main__":
    MprisYoutube().run()
    print 'Good bye :)'

