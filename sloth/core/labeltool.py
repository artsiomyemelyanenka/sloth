#!/usr/bin/python
"""
This is the core labeltool module.


"""

import sys, os
import fnmatch
from optparse import OptionParser
from PyQt4.QtGui import *
from PyQt4.QtCore import *
from sloth.annotations.model import *
from sloth.annotations.container import AnnotationContainerFactory, AnnotationContainer
from sloth.conf import config
from sloth.core.cli import LaxOptionParser, BaseCommand
from sloth.core.utils import import_callable
from sloth import VERSION
from sloth.core.commands import get_commands

import okapy.videoio as okv

class LabelTool(QObject):
    """
    This is the main label tool object.  It stores the state of the tool, i.e.
    the current annotations, the containers responsible for loading and saving
    etc.

    It is also responsible for parsing command line options, call respective
    commands or start the gui.
    """
    usage = "\n" + \
            "  %prog [options] [filename]\n\n" + \
            "  %prog subcommand [options] [args]\n"

    help_text = "Sloth can be started in two different ways.  If the first argument\n" + \
                "is any of the following subcommands, this command is executed.  Otherwise the\n" + \
                "sloth GUI is started and the optionally given label file is loaded.\n" + \
                "\n" + \
                "Type '%s help <subcommand>' for help on a specific subcommand.\n\n"

    # Signals
    statusMessage       = pyqtSignal(QString)
    annotationsLoaded   = pyqtSignal()
    pluginLoaded        = pyqtSignal(QAction)
    # This still emits a QModelIndex, because Qt cannot handle emiting
    # a derived class instead of a base class, i.e. ImageFileModelItem
    # instead of ModelItem
    currentImageChanged = pyqtSignal(QModelIndex)

    # TODO clean up --> prefix all members with _
    def __init__(self, parent=None):
        """
        Constructor.  Does nothing except resetting everything.
        Initialize the labeltool with either::

            execute_from_commandline()

        or::

            init_from_config()
        """
        QObject.__init__(self, parent)

        self.container_factory_ = None
        self.container_ = AnnotationContainer()
        self._current_image = None
        self._model = None

    def main_help_text(self):
        """
        Returns the labeltool's main help text, as a string.

        Includes a list of all available subcommands.
        """
        usage = self.help_text % self.prog_name
        usage += 'Available subcommands:\n'
        commands = get_commands().keys()
        commands.sort()
        for cmd in commands:
            usage += '  %s\n' % cmd
        return usage

    def execute_from_commandline(self, argv=None):
        """
        TODO
        """
        self.argv = argv or sys.argv[:]
        self.prog_name = os.path.basename(argv[0])

        # Preprocess options to extract --settings and --pythonpath.
        # These options could affect the commands that are available, so they
        # must be processed early.
        parser = LaxOptionParser(usage=self.usage,
                                 version=VERSION,
                                 option_list=BaseCommand.option_list)
        try:
            options, args = parser.parse_args(self.argv)
            handle_default_options(options)
        except:
            pass  # Ignore any option errors at this point.

        # Handle options common for all commands
        # and initialize the labeltool object from
        # the configuration (default config if not specified)
        if options.pythonpath:
            sys.path.insert(0, options.pythonpath)
        self.init_from_config(options.config)

        # check for commands
        try:
            subcommand = self.argv[1]
        except IndexError:
            subcommand = None

        # handle commands and command line arguments
        if subcommand == 'help':
            if len(args) > 2:
                self.fetch_command(args[2]).print_help(self.prog_name, args[2])
                sys.exit(0)
            else:
                sys.stdout.write(self.main_help_text() + '\n')
                parser.print_lax_help()
                sys.exit(1)

        elif self.argv[1:] == ['--version']:
            # LaxOptionParser already takes care of printing the version.
            sys.exit(0)

        elif self.argv[1:] in (['--help'], ['-h']):
            sys.stdout.write(self.main_help_text() + '\n')
            parser.print_lax_help()
            sys.exit(0)

        elif subcommand in get_commands():
            self.fetch_command(subcommand).run_from_argv(self.argv)
            sys.exit(0)

        else:
            # no command is matching, then -- if not empty --
            # args must contain a labelfile filename to load
            if len(args) > 1:
                self.loadAnnotations(args[1])
            else:
                self.clearAnnotations()

    def fetch_command(self, subcommand):
        """
        Tries to fetch the given subcommand, printing a message with the
        appropriate command called from the command line if it can't be found.
        """
        try:
            app_name = get_commands()[subcommand]
        except KeyError:
            sys.stderr.write("Unknown command: %r\nType '%s help' for usage.\n" % \
                (subcommand, self.prog_name))
            sys.exit(1)
        if isinstance(app_name, BaseCommand):
            # If the command is already loaded, use it directly.
            klass = app_name
        else:
            # TODO implement load_command_class
            klass = load_command_class(app_name, subcommand)

        # set labeltool reference
        klass.labeltool = self

        return klass

    def init_from_config(self, config_module_path=""):
        """
        Initializes the labeltool from the given configuration
        at ``config_module_path``.  If empty, the default configuration
        is used.
        """
        # Load config
        if config_module_path:
            config.update(config_module_path)

        # Instatiate container factory
        self.container_factory_ = AnnotationContainerFactory(config.CONTAINERS)

        # Load plugins
        self.loadPlugins(config.PLUGINS)

    def loadPlugins(self, plugins):
        self.plugins_ = []
        for plugin in plugins:
            if type(plugin) == str:
                plugin = import_callable(plugin)
            p = plugin(self)
            self.plugins_.append(p)
            action = p.action()
            self.pluginLoaded.emit(action)

    ###
    ### Annoation file handling
    ###___________________________________________________________________________________________
    def loadAnnotations(self, fname):
        fname = str(fname) # convert from QString
        try:
            self.container_ = self.container_factory_.create(fname)
            self.container_.load(fname)
            msg = "Successfully loaded %s (%d files, %d annotations)" % \
                    (fname, self.container_.numFiles(), self.container_.numAnnotations())
            self._model = AnnotationModel(self.container_.annotations())
        except Exception, e:
            msg = "Error: Loading failed (%s)" % str(e)

        self.statusMessage.emit(msg)
        self.annotationsLoaded.emit()

    def saveAnnotations(self, fname):
        success = False
        try:
            # create new container if the filename is different
            if fname != self.container_.filename():
                # TODO: skip if it is the same class
                newcontainer = self.container_factory_.create(fname)
                newcontainer.setAnnotations(self.container_.annotations())
                self.container_ = newcontainer

            self.container_.save(fname)
            #self._model.writeback() # write back changes that are cached in the model itself, e.g. mask updates
            msg = "Successfully saved %s (%d files, %d annotations)" % \
                    (fname, self.container_.numFiles(), self.container_.numAnnotations())
            success = True
            self._model.setDirty(False)
        except Exception as e:
            msg = "Error: Saving failed (%s)" % str(e)

        self.statusMessage.emit(msg)
        return success

    def clearAnnotations(self):
        self.container_.clear()
        self._model = AnnotationModel(self.container_.annotations())
        #self._model.setBasedir("")
        self.statusMessage.emit('')
        self.annotationsLoaded.emit()

    def getCurrentFilename(self):
        return self.container_.filename()

    ###########################################################################
    # Model stuff
    ###########################################################################

    def model(self):
        return self._model

    def gotoNext(self):
        # TODO move this to the scene
        if self._model is not None and self._current_image is not None:
            next_image = self._current_image.getNextSibling()
            self.setCurrentImage(next_image)

    def gotoPrevious(self):
        # TODO move this to the scene
        if self._model is not None and self._current_image is not None:
            prev_image = self._current_image.getPreviousSibling()
            self.setCurrentImage(prev_image)

    def updateModified(self):
        """update all GUI elements which depend on the state of the model,
        e.g. whether it has been modified since the last save"""
        #self.ui.action_Add_Image.setEnabled(self._model is not None)
        # TODO also disable/enable other items
        #self.ui.actionSave.setEnabled(self.annotations.dirty())
        #self.setWindowModified(self.annotations.dirty())
        pass

    def currentImage(self):
        return self._current_image

    def setCurrentImage(self, image):
        if isinstance(image, QModelIndex):
            image = self._model.itemFromIndex(image)
        while (image is not None) and (not isinstance(image, ImageModelItem)):
            image = image.parent()
        if image is None:
            raise RuntimeError("Tried to set current image to item that has no Image or Frame as parent!")
        if image != self._current_image:
            self._current_image = image
            self.currentImageChanged.emit(self._current_image.index())

    def getImage(self, item):
        # TODO: Also handle video frames
        return self.container_.loadImage(item.filename())

    def getAnnotationFilePatterns(self):
        return self.container_factory_.patterns()

    def addImageFile(self, fname):
        fileitem = {
                'filename': fname,
                'type': 'image',
                'annotations': [ ],
            }
        self._model._root.appendFileItem(fileitem)

    def addVideoFile(self, fname):
        fileitem = {
                'filename': fname,
                'type': 'video',
                'frames': [ ],
            }

        # FIXME: OKAPI should provide a method to get all timestamps at once
        # FIXME: Some dialog should be displayed, telling the user that the
        # video is being loaded/indexed and that this might take a while
        video = okv.FFMPEGIndexedVideoSource(fname)
        i = 0
        while video.getNextFrame():
            ts = video.getTimestamp()
            frame = { 'annotations': [],
                      'num': i,
                      'timestamp': ts,
                    }
            fileitem['frames'].append(frame)
            i += 1

        self._model._root.appendFileItem(fileitem)