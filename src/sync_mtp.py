# Copyright (c) 2014-2016 Cedric Bellegarde <cedric.bellegarde@adishatz.org>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from gi.repository import GLib, Gio, Gst

import os.path
from time import sleep

from lollypop.define import Lp
from lollypop.objects import Track


class MtpSync:
    """
        Synchronisation to MTP devices
    """
    def __init__(self):
        """
            Init MTP synchronisation
        """
        self._syncing = False
        self._errors = False
        self._convert = False
        self._errors_count = 0
        self._total = 0  # Total files to sync
        self._done = 0   # Handled files on sync
        self._fraction = 0.0
        self._copied_art_uris = []

############
# Private  #
############
    def _retry(self, func, args, t=5):
        """
            Try to execute func 5 times
            @param func as function
            @param args as tuple
        """
        # Max allowed errors
        if self._errors_count > 10:
            self._syncing = False
            return
        if t == 0:
            self._errors_count += 1
            self._errors = True
            return
        try:
            func(*args)
        except Exception as e:
            print("MtpSync::_retry(%s, %s): %s" % (func, args, e))
            for a in args:
                if isinstance(a, Gio.File):
                    print(a.get_uri())
            sleep(5)
            self._retry(func, args, t-1)

    def _get_tracks_files(self):
        """
            Return children uris for uri
            @return [str]
        """
        children = []
        dir_uris = [self._uri+'/tracks/']
        while dir_uris:
            uri = dir_uris.pop(0)
            album_name = uri.replace(self._uri+"/tracks/", "")
            album = GLib.uri_escape_string(album_name,
                                           "",
                                           False)
            d = Gio.File.new_for_uri(self._uri+"/tracks/"+album)
            infos = d.enumerate_children(
                'standard::name,standard::type',
                Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                None)
            for info in infos:
                if info.get_file_type() == Gio.FileType.DIRECTORY:
                    dir_uris.append(uri+info.get_name())
                else:
                    track = GLib.uri_escape_string(info.get_name(),
                                                   "",
                                                   False)
                    children.append("%s/tracks/%s/%s" % (self._uri,
                                                         album,
                                                         track))
        return children

    def _sync(self, playlists, convert):
        """
            Sync playlists with device as this
            @param playlists as [str]
            @param convert as bool
        """
        try:
            self._in_thread = True
            self._convert = convert
            self._errors = False
            self._errors_count = 0
            self._copied_art_uris = []
            # For progress bar
            self._total = 1
            self._done = 0
            self._fraction = 0.0
            plnames = []

            # New tracks
            for playlist in playlists:
                plnames.append(Lp().playlists.get_name(playlist))
                self._fraction = self._done/self._total
                self._total += len(Lp().playlists.get_tracks(playlist))

            # Old tracks
            try:
                children = self._get_tracks_files()
                self._total += len(children)
            except:
                pass
            GLib.idle_add(self._update_progress)

            # Copy new tracks to device
            if self._syncing:
                self._copy_to_device(playlists)

            # Remove old tracks from device
            if self._syncing:
                self._remove_from_device(playlists)

            # Delete old playlists
            d = Gio.File.new_for_uri(self._uri)
            infos = d.enumerate_children(
                'standard::name',
                Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                None)
            for info in infos:
                f = info.get_name()
                if f.endswith(".m3u") and f[:-4] not in plnames:
                    uri = self._uri+'/'+f
                    d = Gio.File.new_for_uri(uri)
                    self._retry(d.delete, (None,))

        except Exception as e:
            print("DeviceManagerWidget::_sync(): %s" % e)
        self._fraction = 1.0
        if self._syncing:
            self._syncing = False
        else:
            GLib.idle_add(self._on_finished)
        self._in_thread = False
        if self._errors:
            GLib.idle_add(self._on_errors)

    def _copy_to_device(self, playlists):
        """
            Copy file from playlist to device
            @param playlists as [str]
        """
        for playlist in playlists:
            try:
                playlist_name = Lp().playlists.get_name(playlist)
                # Create playlist
                m3u = Gio.File.new_for_path(
                    "/tmp/lollypop_%s.m3u" % (playlist_name,))
                self._retry(m3u.replace_contents, (b'#EXTM3U\n', None, False,
                            Gio.FileCreateFlags.REPLACE_DESTINATION,
                            None))
                stream = m3u.open_readwrite(None)
            except Exception as e:
                print("DeviceWidget::_copy_to_device(): %s" % e)
                m3u = None
                stream = None

            # Start copying
            tracks_ids = Lp().playlists.get_tracks_ids(playlist)
            for track_id in tracks_ids:
                if track_id is None:
                    continue
                if not self._syncing:
                    self._fraction = 1.0
                    self._in_thread = False
                    return
                track = Track(track_id)
                album_name = GLib.uri_escape_string(track.album_name.lower(),
                                                    "",
                                                    False)
                artist_name = GLib.uri_escape_string(track.artist.lower(),
                                                     "",
                                                     False)
                on_device_album_uri = "%s/tracks/%s_%s" %\
                                      (self._uri,
                                       artist_name,
                                       album_name)

                d = Gio.File.new_for_uri(on_device_album_uri)
                if not d.query_exists(None):
                    self._retry(d.make_directory_with_parents, (None,))
                # Copy album art
                art = Lp().art.get_album_artwork_path(track.album)
                if art is not None:
                    src_art = Gio.File.new_for_path(art)
                    art_uri = "%s/cover.jpg" % on_device_album_uri
                    self._copied_art_uris.append(art_uri)
                    dst_art = Gio.File.new_for_uri(art_uri)
                    if not dst_art.query_exists(None):
                        self._retry(src_art.copy,
                                    (dst_art, Gio.FileCopyFlags.OVERWRITE,
                                     None, None))

                track_name = GLib.uri_escape_string(GLib.basename(track.path),
                                                    "",
                                                    False)
                # Check extension, if not mp3, convert
                ext = os.path.splitext(track.path)[1]
                if ext != ".mp3" and self._convert:
                    convertion_needed = True
                    track_name = track_name.replace(ext, ".mp3")
                else:
                    convertion_needed = False
                src_track = Gio.File.new_for_path(track.path)
                info = src_track.query_info('time::modified',
                                            Gio.FileQueryInfoFlags.NONE,
                                            None)
                # Prefix track with mtime to make sure updating it later
                mtime = info.get_attribute_as_string('time::modified')
                dst_uri = "%s/%s_%s" % (on_device_album_uri,
                                        mtime, track_name)
                if stream is not None:
                    line = "tracks/%s_%s/%s_%s\n" %\
                            (artist_name.lower(),
                             album_name.lower(),
                             mtime,
                             track_name)
                    self._retry(stream.get_output_stream().write,
                                (line.encode(encoding='UTF-8'), None))
                dst_track = Gio.File.new_for_uri(dst_uri)
                if not dst_track.query_exists(None):
                    if convertion_needed:
                        mp3_uri = "file:///tmp/%s" % track_name
                        mp3_file = Gio.File.new_for_uri(mp3_uri)
                        pipeline = self._convert_to_mp3(src_track, mp3_file)
                        # Check if encoding is finished
                        if pipeline is not None:
                            bus = pipeline.get_bus()
                            bus.add_signal_watch()
                            bus.connect('message::eos', self._on_bus_eos)
                            self._encoding = True
                            while self._encoding and self._sync:
                                sleep(1)
                            bus.disconnect_by_func(self._on_bus_eos)
                            pipeline.set_state(Gst.State.PAUSED)
                            pipeline.set_state(Gst.State.READY)
                            pipeline.set_state(Gst.State.NULL)
                            self._retry(
                                    mp3_file.move,
                                    (dst_track, Gio.FileCopyFlags.OVERWRITE,
                                     None, None))
                            # To be sure
                            try:
                                mp3_file.delete(None)
                            except:
                                pass
                    else:
                        self._retry(src_track.copy,
                                    (dst_track, Gio.FileCopyFlags.OVERWRITE,
                                     None, None))
                else:
                    self._done += 1
                self._done += 1
                self._fraction = self._done/self._total
            if stream is not None:
                stream.close()
            if m3u is not None:
                playlist_name = GLib.uri_escape_string(playlist_name,
                                                       "",
                                                       False)
                dst = Gio.File.new_for_uri(self._uri+'/'+playlist_name+'.m3u')
                self._retry(m3u.move,
                            (dst, Gio.FileCopyFlags.OVERWRITE, None, None))

    def _remove_from_device(self, playlists):
        """
            Delete files not available in playlist
        """
        track_uris = []
        tracks_ids = []

        # Get tracks ids
        for playlist in playlists:
            tracks_ids += Lp().playlists.get_tracks_ids(playlist)

        # Get tracks uris
        for track_id in tracks_ids:
            if not self._syncing:
                self._fraction = 1.0
                self._in_thread = False
                return
            track = Track(track_id)
            album_name = GLib.uri_escape_string(track.album_name.lower(),
                                                "",
                                                False)
            artist_name = GLib.uri_escape_string(track.artist.lower(),
                                                 "",
                                                 False)
            album_uri = "%s/tracks/%s_%s" % (self._uri,
                                             artist_name,
                                             album_name)
            track_name = GLib.uri_escape_string(GLib.basename(track.path),
                                                "",
                                                False)
            # Check extension, if not mp3, convert
            ext = os.path.splitext(track.path)[1]
            if ext != ".mp3" and self._convert:
                track_name = track_name.replace(ext, ".mp3")
            on_disk = Gio.File.new_for_path(track.path)
            info = on_disk.query_info('time::modified',
                                      Gio.FileQueryInfoFlags.NONE,
                                      None)
            # Prefix track with mtime to make sure updating it later
            mtime = info.get_attribute_as_string('time::modified')
            dst_uri = "%s/%s_%s" % (album_uri, mtime, track_name)
            track_uris.append(dst_uri)

        on_mtp_files = self._get_tracks_files()

        # Delete file on device and not in playlists
        for uri in on_mtp_files:
            if not self._syncing:
                self._fraction = 1.0
                self._in_thread = False
                return

            if uri not in track_uris and uri not in self._copied_art_uris:
                to_delete = Gio.File.new_for_uri(uri)
                self._retry(to_delete.delete, (None,))
            self._done += 1
            self._fraction = self._done/self._total

    def _convert_to_mp3(self, src, dst):
        """
            Convert file to mp3
            @param src as Gio.File
            @param dst as Gio.File
            @return Gst.Pipeline
        """
        try:
            pipeline = Gst.parse_launch('filesrc location="%s" ! decodebin\
                                        ! audioconvert ! lamemp3enc ! id3v2mux\
                                        ! filesink location="%s"'
                                        % (src.get_path(), dst.get_path()))
            pipeline.set_state(Gst.State.PLAYING)
            return pipeline
        except Exception as e:
            print("MtpSync::_convert_to_mp3(): %s" % e)
            return None

    def _update_progress(self):
        """
            Update progress bar. Do nothing
        """
        pass

    def _on_bus_eos(self, bus, message):
        """
            Stop encoding
            @param bus as Gst.Bus
            @param message as Gst.Message
        """
        self._encoding = False

    def _on_finished(self):
        """
            Clean on finished. Do nothing
        """
        pass

    def _on_errors(self):
        """
            Show something to the user. Do nothing.
        """
        pass
