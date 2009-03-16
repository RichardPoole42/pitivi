#!/usr/bin/python
# PiTiVi , Non-linear video editor
#
#       project.py
#
# Copyright (c) 2005, Edward Hervey <bilboed@bilboed.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this program; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

"""
Project class
"""

import os.path
import gst
import traceback
from gettext import gettext as _
from pitivi.log.loggable import Loggable
from pitivi.timeline.timeline import Timeline
from pitivi.timeline.track import Track
from pitivi.stream import AudioStream, VideoStream
from pitivi.pipeline import Pipeline
from pitivi.factories.timeline import TimelineSourceFactory
from pitivi.sourcelist import SourceList
from pitivi.settings import ExportSettings
from pitivi.configure import APPNAME
from pitivi.signalinterface import Signallable
from pitivi.action import ViewAction

class Project(object, Signallable, Loggable):
    """ The base class for PiTiVi projects
    Signals

       boolean save-uri-requested()
            The the current project has been requested to save itself, but
            needs a URI to which to save. Handlers should first call
            setUri(), with the uri to save the file (optionally
            specifying the file format) and return True, or simply
            return False to cancel the file save operation.

        boolean confirm-overwrite()
            The project has been requested to save itself, but the file on
            disk either already exists, or has been changed since the previous
            load/ save operation. In this case, the project wants permition to
            overwrite before continuing. handlers should return True if it is
            ok to overwrit the file, or False otherwise. By default, this
            signal handler assumes True.

        void settings-changed()
            The project settings have changed

    @cvar timeline: The timeline
    @type timeline: L{Timeline}
    @cvar pipeline: The timeline's pipeline
    @type pipeline: L{Pipeline}
    @cvar factory: The timeline factory
    @type factory: L{TimelineSourceFactory}
    """

    __signals__ = {
        "save-uri-requested" : None,
        "confirm-overwrite" : ["location"],
        "settings-changed" : None,
        "missing-plugins": ["uri", "detail", "description"]
        }

    def __init__(self, name="", uri=None, **kwargs):
        """
        name : the name of the project
        uri : the uri of the project
        """
        Loggable.__init__(self)
        self.log("name:%s, uri:%s", name, uri)
        self.name = name
        self.settings = None
        self.description = ""
        self.uri = uri
        self.urichanged = False
        self.format = None
        self.sources = SourceList(self)
        self.settingssigid = 0
        self._dirty = False

        self.sources.connect('missing-plugins', self._sourceListMissingPluginsCb)

        self.timeline = Timeline()

        # FIXME: the tracks should be loaded from the settings
        video = VideoStream(gst.Caps('video/x-raw-rgb; video/x-raw-yuv'))
        track = Track(video)
        self.timeline.addTrack(track)
        audio = AudioStream(gst.Caps('audio/x-raw-int; audio/x-raw-float'))
        track = Track(audio)
        self.timeline.addTrack(track)

        self.factory = TimelineSourceFactory(self.timeline)
        self.pipeline = Pipeline()
        self.view_action = ViewAction()
        self.view_action.addProducers(self.factory)

        # don't want to make calling load() necessary for blank projects
        if self.uri == None:
            self._loaded = True
        else:
            self._loaded = False

    def release(self):
        self.pipeline.release()
        self.pipeline = None

    def load(self):
        """ call this to load a project from a file (once) """
        if self._loaded:
            # should this return false?
            self.warning("Already loaded !!!")
            return True
        try:
            res = self._load()
        except:
            self.error("An Exception was raised during loading !")
            traceback.print_exc()
            res = False
        finally:
            return res

    def _load(self):
        """
        loads the project from a file
        Private method, use load() instead
        """
        self.log("uri:%s", self.uri)
        self.debug("Creating timeline")
        # FIXME : This should be discovered !
        saveformat = "pickle"
        if self.uri and file_is_project(self.uri):
            loader = ProjectSaver.newProjectSaver(saveformat)
            path = gst.uri_get_location(self.uri)
            fileobj = open(path, "r")
            try:
                tree = loader.openFromFile(fileobj)
                self.fromDataFormat(tree)
            except ProjectLoadError:
                self.error("Error while loading the project !!!")
                return False
            finally:
                fileobj.close()
            self.format = saveformat
            self.urichanged = False
            self.debug("Done loading !")
            return True
        return False

    def _save(self):
        """ internal save function """
        if uri_is_valid(self.uri):
            path = gst.uri_get_location(self.uri)
        else:
            self.warning("uri '%s' is invalid, aborting save", self.uri)
            return False

        #TODO: a bit more sophisticated overwite detection
        if os.path.exists(path) and self.urichanged:
            overwriteres = self.emit("confirm-overwrite", self.uri)
            if overwriteres == False:
                self.log("aborting save because overwrite was denied")
                return False

        try:
            fileobj = open(path, "w")
            loader = ProjectSaver.newProjectSaver(self.format)
            tree = self.toDataFormat()
            loader.saveToFile(tree, fileobj)
            self._dirty = False
            self.urichanged = False
            self.log("Project file saved successfully !")
            return True
        except IOError:
            return False

    def save(self):
        """ Saves the project to the project's current file """
        self.log("saving...")
        if self.uri:
            return self._save()

        self.log("requesting for a uri to save to...")
        saveres = self.emit("save-uri-requested")
        if saveres == None or saveres == True:
            self.log("'save-uri-requested' returned True, self.uri:%s", self.uri)
            if self.uri:
                return self._save()

        self.log("'save-uri-requested' returned False or uri wasn't set, aborting save")
        return False

    def saveAs(self):
        """ Saves the project to the given file name """
        if not self.emit("save-uri-requested"):
            return False
        if not self.uri:
            return False
        return self._save()

    # setting methods
    def _settingsChangedCb(self, unused_settings):
        self.emit('settings-changed')

    def setUri(self, uri, format=None):
        """ Set the location to which this project will be stored """
        self.log("uri:%s, format:%s", uri, format)
        if not self.uri == uri:
            self.log("updating self.uri, previously:%s", self.uri)
            self.uri = uri
            self.urichanged = True

        if not format or not self.format == format:
            self.log("updating save format, previously:%s", self.format)
            if not format:
                path = gst.uri_get_location(uri)
                ext = os.path.splitext(path)[1]
                self.log("Based on file extension, format is %s", format)
                format = ProjectSaver.getFormat(ext)
            self.format = format

    def getSettings(self):
        """
        return the currently configured settings.
        If no setting have been explicitely set, some smart settings will be
        chosen.
        """
        return self.settings or self.getAutoSettings()

    def setSettings(self, settings):
        """
        Sets the given settings as the project's settings.
        If settings is None, the current settings will be unset
        """
        self.log("Setting %s as the project's settings", settings)
        if self.settings:
            self.settings.disconnect(self.settingssigid)
        self.settings = settings
        self.emit('settings-changed')
        self.settingssigid = self.settings.connect('settings-changed',
            self._settingsChangedCb)

    def unsetSettings(self, unused_settings):
        """ Remove the currently configured settings."""
        self.setSettings(None)

    def getAutoSettings(self):
        """
        Computes and returns smart settings for the project.
        If the project only has one source, it will be that source's settings.
        If it has more than one, it will return the largest setting that suits
        all contained sources.
        """
        settings = ExportSettings()
        if not self.timeline:
            self.warning("project doesn't have a timeline, returning default settings")
            return settings

        # FIXME: this is ugly, but rendering for now assumes at most one audio
        # and one video tracks
        have_audio = have_video = False
        for track in self.timeline.tracks:
            if isinstance(track.stream, VideoStream) and track.duration != 0:
                have_video = True
            elif isinstance(track.stream, AudioStream) and track.duration != 0:
                have_audio = True

        if not have_audio:
            settings.aencoder = None

        if not have_video:
            settings.vencoder = None

        return settings

    def setModificationState(self, state):
        self._dirty = state

    def hasUnsavedModifications(self):
        return self._dirty

    def _sourceListMissingPluginsCb(self, source_list, uri, detail, description):
        return self.emit('missing-plugins', uri, detail, description)

def uri_is_valid(uri):
    """ Checks if the given uri is a valid uri (of type file://) """
    return gst.uri_get_protocol(uri) == "file"

def file_is_project(uri):
    """ returns True if the given uri is a PitiviProject file"""
    if not uri_is_valid(uri):
        raise NotImplementedError(
            _("%s doesn't yet handle non local projects") % APPNAME)
    return os.path.isfile(gst.uri_get_location(uri))
