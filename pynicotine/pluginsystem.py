# -*- coding: utf-8 -*-
import os
import sys
import gtk
import gobject
import zipimport, imp
from thread import start_new_thread
from traceback import extract_stack, extract_tb, format_list
import traceback
from pynicotine import slskmessages
from slskmessages import ToBeEncoded
from utils import _
from logfacility import log

WIN32 = sys.platform.startswith("win")

returncode = {'break':0, # don't give other plugins the event, do let n+ process it
              'zap':1,   # don't give other plugins the event, don't let n+ process it
              'pass':2}  # do give other plugins the event, do let n+ process it
                         # returning nothing is the same as 'pass'
tupletype = type(('',''))

def cast_to_unicode_if_needed(text, logfunc):
	if type(text) == type(u''):
		return text
	try:
		better = str.decode(text, 'utf8')
		logfunc("Plugin problem: casting '%s' to unicode!" % repr(text))
		return better
	except UnicodeError:
		better = str.decode(text, 'utf8', 'replace')
		logfunc("Plugin problem: casting '%s' to unicode, losing characters in the process." % repr(text))
		return better
	except:
		logfunc("Plugin problem: failed to completely cast '%s', you're on your own from here on." % repr(text))
		return text

class PluginHandler(object):
	frame = None # static variable... but should it be?
	guiqueue = [] # fifo isn't supported by older python
	def __init__(self, frame, plugindir=None):
		self.frame = frame
		log.add("Loading plugin handler")
		self.myUsername = self.frame.np.config.sections["server"]["login"]
		self.plugindirs = []
		self.enabled_plugins = {}
		self.loaded_plugins = {}
		self.type2cast = {'integer':int,'int':int, 'float':float, 'string':str,'str':str, }
		
		if not plugindir:
			if WIN32:
				try:
					mydir = os.path.join(os.environ['APPDATA'], 'nicotine')
				except KeyError:
					# windows 9x?
					mydir,x = os.path.split(sys.argv[0])
				self.plugindir = os.path.join(mydir, "plugins")
			else:
				self.plugindir = os.path.join(os.path.expanduser("~"),'.nicotine','plugins')
		else:
			self.plugindir = plugindir
		try:
			os.makedirs(self.plugindir)
		except:
			pass
		self.plugindirs.append(self.plugindir)
		if os.path.isdir(self.plugindir):
			#self.load_directory(self.plugindir)
			self.load_enabled()
		else:
			log.add("It appears '%s' is not a directory, not loading plugins." % self.plugindir)

	def __findplugin(self, pluginname):
		for directory in self.plugindirs:
			fullpath = os.path.join(directory, pluginname)
			if os.path.exists(fullpath):
				return fullpath
		return None

	def load_plugin(self, pluginname, reload=False):
		if not reload and pluginname in self.loaded_plugins:
			return self.loaded_plugins[pluginname]

		path = self.__findplugin(pluginname)
		if path is None:
			log.add("Failed to load plugin '%s', could not find it." % (pluginname, ))
			return False
		sys.path.insert(0, path)
		plugin = imp.load_source(pluginname, os.path.join(path,'__init__.py'))
		instance = plugin.Plugin(self)
		self.plugin_settings(instance)
		instance.LoadNotification()
		#log.add("Loaded plugin %s (version %s) from %s" % (instance.__name__, instance.__version__, modulename))
		#self.plugins.append((module, instance))
		sys.path = sys.path[1:]
		self.loaded_plugins[pluginname] = plugin
		return plugin
		
	def install_plugin(self, path):
		try:
			tar = tarfile.open(path, "r:*") #transparently supports gz, bz2
		except (tarfile.ReadError, OSError):
			raise InvalidPluginError(_('Plugin archive is not in the correct format'))

		#ensure the paths in the archive are sane
		mems = tar.getmembers()
		base = os.path.basename(path)[:-4]
		if os.path.isdir(os.path.join(self.plugindirs[0], base)):
			raise InvalidPluginError(_('A plugin with the name "%s" is '
				'already installed') % base)

		for m in mems:
			if not m.name.startswith(base):
				raise InvalidPluginError(_("Plugin archive contains an unsafe path"))

		tar.extractall(self.plugindirs[0])

	def uninstall_plugin(self, pluginname):
		self.disable_plugin(pluginname)
		for dir in self.plugindirs:
			try:
				shutil.rmtree(self.__findplugin(pluginname))
				return True
			except:
				pass
		return False

	def enable_plugin(self, pluginname):
		if pluginname in self.enabled_plugins:
			return
		try:
			plugin = self.load_plugin(pluginname)
			if not plugin: raise Exception("Error loading plugin '%s'" % pluginname)
			plugin.enable(self)
			self.enabled_plugins[pluginname] = plugin
			log.add(_("Loaded plugin %s") % plugin.PLUGIN.__name__)
		except:
			traceback.print_exc()
			log.addwarning(_("Unable to enable plugin %s")%pluginname)
			#common.log_exception(logger)
			return False
		return True
		
	def list_installed_plugins(self):
		pluginlist = []
		for dir in self.plugindirs:
			if os.path.exists(dir):
				for file in os.listdir(dir):
					if file not in pluginlist and os.path.isdir(os.path.join(dir, file)):
						pluginlist.append(file)
		return pluginlist

	def disable_plugin(self, pluginname):
		try:
			plugin = self.enabled_plugins[pluginname]
			del self.enabled_plugins[pluginname]
			plugin.disable(self)
		except:
			traceback.print_exc()
			log.addwarning(_("Unable to fully disable plugin %s")%pluginname)
			#common.log_exception(logger)
			return False
		return True
		
	def get_plugin_settings(self, pluginname):
		if pluginname in self.enabled_plugins:
			plugin = self.enabled_plugins[pluginname]
			if hasattr(plugin.PLUGIN, "metasettings"):
				return plugin.PLUGIN.metasettings

	def get_plugin_info(self, pluginname):
		path = os.path.join(self.__findplugin(pluginname), 'PLUGININFO')
		f = open(path)
		infodict = {}
		for line in f:
			try:
				key, val = line.split("=",1)
				infodict[key] = eval(val)
			except ValueError:
				pass # this happens on blank lines
		return infodict


	def save_enabled(self):
		self.frame.np.config.sections["plugins"]["enabled"] = self.enabled_plugins.keys()

	def check_enabled(self):
		if self.frame.np.config.sections["plugins"]["enable"]:
			self.load_enabled()
		else:
			to_enable = self.frame.np.config.sections["plugins"]["enabled"]
			for plugin in self.enabled_plugins:
				self.enabled_plugins[plugin].disable(self)
				print "Disabled plugin: %s" %plugin
	def load_enabled(self):
		enable = self.frame.np.config.sections["plugins"]["enable"]
		if not enable:
			return
		to_enable = self.frame.np.config.sections["plugins"]["enabled"]
		for plugin in to_enable:
			self.enable_plugin(plugin)
			
	def plugin_settings(self, plugin):
		try:
			#customsettings = self.frame.np.config.sections["plugins"][plugin.__id__]
			if not hasattr(plugin, "settings"):
				return
			if plugin.__id__ not in self.frame.np.config.sections["plugins"]:
				self.frame.np.config.sections["plugins"][plugin.__id__] = plugin.settings
			for i in plugin.settings:
				if i not in self.frame.np.config.sections["plugins"][plugin.__id__]:
					self.frame.np.config.sections["plugins"][plugin.__id__][i] = plugin.settings[i]
			customsettings = self.frame.np.config.sections["plugins"][plugin.__id__]
			#if customsettings = self.frame.np.config.sections["plugins"][plugin.__id__]
			#for settingname, info in plugin.metasettings.items():
				
				#if settingname not in ('<hr>',):
					#settingdescr = info["description"]
					#settingtype = info["type"]
					#try:
						#value = customsettings[settingname]
						#try:
							#if settingtype.startswith('list '):
								#value = list(value)
								#(junk, junk, listtype) = settingtype.partition(' ')
								#index = 0
								#for index in xrange(0, len(value)):
									#value[index] = self.type2cast[listtype](value[index])
							#else:
								#value = self.type2cast[settingtype](value)
								#plugin.settings[settingname] = value
						#except ValueError:
							#log.add(_("Failed to cast the value '%(value)s', stored under '%(name)s', to %(type)s. Using default value." %
								#{'value':value, 'name':settingname, 'type':settingtype}))
						#except KeyError:
							#log.add(_("Unknown setting type '%(type)s'." % {'type':settingtype}))
					#except KeyError:
						#pass
			for key in customsettings:
				if key in plugin.settings:
					plugin.settings[key] = customsettings[key]
					
				else:
					log.add(_("Stored setting '%(name)s' is no longer present in the plugin") % {'name':key})
		except KeyError:
			log.add("No custom settings found for %s" % (plugin.__name__,))
			pass
	
	def TriggerPublicCommandEvent(self, room, command, args):
		return self._TriggerCommand("plugin.PLUGIN.PublicCommandEvent", command, room, args)
	def TriggerPrivateCommandEvent(self, user, command, args):
		return self._TriggerCommand("plugin.PLUGIN.PrivateCommandEvent", command, user, args)
	def _TriggerCommand(self, strfunc, command, source, args):
		for module, plugin in self.enabled_plugins.items():
			try:
				if plugin.PLUGIN is None:
					continue
				func = eval(strfunc)
				ret = func(command, source, args)
				if ret is not None:
					if ret == returncode['zap']:
						return True
					elif ret == returncode['pass']:
						pass
					else:
						log.add(_("Plugin %(module)s returned something weird, '%(value)s', ignoring") % {'module':module, 'value':str(ret)})
			except:
				log.add(_("Plugin %(module)s failed with error %(errortype)s: %(error)s.\nTrace: %(trace)s\nProblem area:%(area)s") %
					{'module':module,
					'errortype':sys.exc_info()[0],
					'error':sys.exc_info()[1],
					'trace':''.join(format_list(extract_stack())),
					'area':''.join(format_list(extract_tb(sys.exc_info()[2])))})
		return False
	def TriggerEvent(self, function, args):
		"""Triggers an event for the plugins. Since events and notifications
		are precisely the same except for how n+ responds to them, both can be
		triggered by this function."""
		hotpotato = args
		for module, plugin in self.enabled_plugins.items():
			try:
				func = eval("plugin.PLUGIN." + function)
				ret = func(*hotpotato)
				if ret != None and type(ret) != tupletype:
					if ret == returncode['zap']:
						return None
					elif ret == returncode['break']:
						return hotpotato
					elif ret == returncode['pass']:
						pass
					else:
						log.add(_("Plugin %(module) returned something weird, '%(value)', ignoring") % {'module':module, 'value':ret})
				if ret != None:
					hotpotato = ret
			except:
				log.add(_("Plugin %(module)s failed with error %(errortype)s: %(error)s.\nTrace: %(trace)s\nProblem area:%(area)s") %
					{'module':module,
					'errortype':sys.exc_info()[0],
					'error':sys.exc_info()[1],
					'trace':''.join(format_list(extract_stack())),
					'area':''.join(format_list(extract_tb(sys.exc_info()[2])))})
		return hotpotato
	def IncomingPrivateChatEvent(self, user, line):
		if user != self.myUsername:
		# dont trigger the scripts on our own talking - we've got "Outgoing" for that
			return self.TriggerEvent("IncomingPrivateChatEvent", (user, line))
		else:
			return (user, line)
	def IncomingPrivateChatNotification(self, user, line):
		start_new_thread(self.TriggerEvent, ("IncomingPrivateChatNotification", (user, line)))
	def IncomingPublicChatEvent(self, room, user, line):
		return self.TriggerEvent("IncomingPublicChatEvent", (room, user, line))
	def IncomingPublicChatNotification(self, room, user, line):
		start_new_thread(self.TriggerEvent, ("IncomingPublicChatNotification", (room, user, line)))
	def OutgoingPrivateChatEvent(self, user, line):
		if line != None:
		# if line is None nobody actually said anything
			return self.TriggerEvent("OutgoingPrivateChatEvent", (user, line))
		else:
			return (user, line)
	def OutgoingPrivateChatNotification(self, user, line):
		start_new_thread(self.TriggerEvent, ("OutgoingPrivateChatNotification", (user, line)))
	def OutgoingPublicChatEvent(self, room, line):
		return self.TriggerEvent("OutgoingPublicChatEvent", (room, line))
	def OutgoingPublicChatNotification(self, room, line):
		start_new_thread(self.TriggerEvent, ("OutgoingPublicChatNotification", (room, line)))
	def OutgoingGlobalSearchEvent(self, text):
		return self.TriggerEvent("OutgoingGlobalSearchEvent", (text,))
	def OutgoingRoomSearchEvent(self, rooms, text):
		return self.TriggerEvent("OutgoingRoomSearchEvent", (rooms, text))
	def OutgoingBuddySearchEvent(self, text):
		return self.TriggerEvent("OutgoingBuddySearchEvent", (text,))
	def OutgoingUserSearchEvent(self, users):
		return self.TriggerEvent("OutgoingUserSearchEvent", (users,))
	def UserResolveNotification(self, user, ip, port, country=None):
		"""Notification for user IP:Port resolving.

		Note that country is only set when the user requested the resolving"""
		start_new_thread(self.TriggerEvent, ("UserResolveNotification", (user, ip, port, country)))
	def ServerConnectNotification(self):
		start_new_thread(self.TriggerEvent, ("ServerConnectNotification", (),))
	def ServerDisconnectNotification(self, userchoice):
		start_new_thread(self.TriggerEvent, ("ServerDisconnectNotification", (userchoice, )))
	def JoinChatroomNotification(self, room):
		start_new_thread(self.TriggerEvent, ("JoinChatroomNotification", (room,)))
	def LeaveChatroomNotification(self, room): 
		start_new_thread(self.TriggerEvent, ("LeaveChatroomNotification", (room,)))
	def UploadQueuedNotification(self, user, virtualfile, realfile):
		start_new_thread(self.TriggerEvent, ("UploadQueuedNotification", (user, virtualfile, realfile)))
	def UserStatsNotification(self, user, stats):
		start_new_thread(self.TriggerEvent, ("UserStatsNotification", (user, stats)))
	# other functions
	def appendqueue(self, item):
		# We cannot do a test after adding the item since it's possible
		# this function will be called twice simultaneously - and then
		# len(self.guiqueue) might be 2 for both calls.
		# Calling the processQueue twice is not a problem though.
		addidle = False
		
		self.guiqueue.append(item)
		if len(self.guiqueue) >= 0:
			addidle = True
		if addidle:
			#print "Adding idle_add"
			gobject.idle_add(self.processQueue)
	def log(self, text):
		self.appendqueue({'type':'logtext', 'text':text})
	def saychatroom(self, room, text):
		text = cast_to_unicode_if_needed(text, log.addwarning)
		self.frame.np.queue.put(slskmessages.SayChatroom(room, ToBeEncoded(text, 'UTF-8')))
	def sayprivate(self, user, text):
		'''Send user message in private (showing up in GUI)'''
		self.appendqueue({'type':'sayprivate', 'user':user, 'text':text})
	def sendprivate(self, user, text):
		'''Send user message in private (not showing up in GUI)'''
		self.appendqueue({'type':'sendprivate', 'user':user, 'text':text})
	def processQueue(self):
		while len(self.guiqueue) > 0:
			i = self.guiqueue.pop(0)
			if i['type'] == 'logtext':
				log.add(i['text'])
			elif i['type'] == 'sayprivate':
				# If we use the np the chat lines only show up on the receiving end, we won't see anything ourselves.
				self.frame.privatechats.users[i['user']].SendMessage(i['text'])
			elif i['type'] == 'sendprivate':
				self.frame.privatechats.SendMessage(i['user'], i['text'])
			else:
				log.add(_('Unknown queue item %s: %s' % (i['type'], repr(i))))
		return False

class BasePlugin(object):
	__name__ = "BasePlugin"
	__desc__ = "No description provided"
	#__id__ = "baseplugin_original" # you normally don't have to set this manually
	__version__ = "2008-11-26"
	__publiccommands__ = []
	__privatecommands__ = []
	def __init__(self, parent):
		# Never override this function, override init() instead
		self.parent = parent
		self.frame = parent.frame
		try:
			self.__id__
		except AttributeError:
			# See http://docs.python.org/library/configparser.html
			# %(name)s will lead to replacements so we need to filter out those symbols.
			self.__id__ = self.__name__.lower().replace(' ', '_').replace('%', '_').replace('=', '_')
		self.init()
		for (trigger, func) in self.__publiccommands__:
			self.frame.chatrooms.roomsctrl.CMDS.add('/'+trigger+' ')
		for (trigger, func) in self.__privatecommands__:
			self.frame.privatechats.CMDS.add('/'+trigger+' ')
	def init(self):
		pass
	def LoadSettings(self, settings):
		self.settings = settings
	def LoadNotification(self):
		pass
	def IncomingPrivateChatEvent(self, user, line):
		pass
	def IncomingPrivateChatNotification(self, user, line):
		pass
	def IncomingPublicChatEvent(self, room, user, line):
		pass
	def IncomingPublicChatNotification(self, room, user, line):
		pass
	def OutgoingPrivateChatEvent(self, user, line):
		pass
	def OutgoingPrivateChatNotification(self, user, line):
		pass
	def OutgoingPublicChatEvent(self, room, line):
		pass
	def OutgoingPublicChatNotification(self, room, line):
		pass
	def OutgoingGlobalSearchEvent(self, text):
		pass
	def OutgoingRoomSearchEvent(self, rooms, text):
		pass
	def OutgoingBuddySearchEvent(self, text):
		pass
	def OutgoingUserSearchEvent(self, users):
		pass
	def UserResolveNotification(self, user, ip, port, country):
		pass
	def ServerConnectNotification(self):
		pass
	def ServerDisconnectNotification(self, userchoice):
		pass
	def JoinChatroomNotification(self, room):
		pass
	def LeaveChatroomNotification(self, room):
		pass
	def UploadQueuedNotification(self, user, virtualfile, realfile):
		pass
	def UserStatsNotification(self, user, stats):
		pass
	# The following are functions to make your life easier,
	# you shouldn't override them.
	def log(self, text):
		self.parent.log(self.__name__ + ": " + text)
	def saypublic(self, room, text):
		self.parent.saychatroom(room, text)
	def sayprivate(self, user, text):
		'''Send user message in private (shows up in GUI)'''
		self.parent.sayprivate(user, text)
	def sendprivate(self, user, text):
		'''Send user message in private (doesn't show up in GUI)'''
		self.parent.sendprivate(user, text)
	def fakepublic(self, room, user, text):
		try:
			room = self.frame.chatrooms.roomsctrl.joinedrooms[room]
		except KeyError:
			return False
		text = cast_to_unicode_if_needed(text, self.log)
		msg = slskmessages.SayChatroom(room, ToBeEncoded(text, 'UTF-8'))
		msg.user = user
		room.SayChatRoom(msg, text)
		return True
	# The following are functions used by the plugin system,
	# you are not allowed to override these.
	def PublicCommandEvent(self, command, room, args):
		for (trigger, func) in self.__publiccommands__:
			if trigger == command:
				return func(self, room, args)
	def PrivateCommandEvent(self, command, user, args):
		for (trigger, func) in self.__privatecommands__:
			if trigger == command:
				return func(self, user, args)
