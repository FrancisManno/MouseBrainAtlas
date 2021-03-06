#! /usr/bin/env python

import sip
sip.setapi('QVariant', 2) # http://stackoverflow.com/questions/21217399/pyqt4-qtcore-qvariant-object-instead-of-a-string

import sys
import os
import datetime
from random import random
import subprocess
import time
import json
from pprint import pprint
import cPickle as pickle
from itertools import groupby
from operator import itemgetter

import numpy as np

from matplotlib.backends import qt4_compat
use_pyside = qt4_compat.QT_API == qt4_compat.QT_API_PYSIDE
if use_pyside:
	#print 'Using PySide'
	from PySide.QtCore import *
	from PySide.QtGui import *
else:
	#print 'Using PyQt4'
	from PyQt4.QtCore import *
	from PyQt4.QtGui import *

from ui_BrainLabelingGui_v13 import Ui_BrainLabelingGui

from matplotlib.colors import ListedColormap, NoNorm, ColorConverter

from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import LineString as ShapelyLineString
from shapely.geometry import LinearRing as ShapelyLineRing

from skimage.color import label2rgb

from visualization_utilities import *

sys.path.append(os.environ['REPO_DIR'] + '/utilities')
from utilities2015 import *

from collections import defaultdict, OrderedDict, deque
from scipy.spatial.distance import cdist

from operator import attrgetter

import requests

from joblib import Parallel, delayed

# from LabelingPainter import LabelingPainter
from custom_widgets import *
from SignalEmittingItems import *

from enum import Enum

class Mode(Enum):
	REVIEW_PROPOSAL = 'review proposal'
	IDLE = 'idle'
	MOVING_POLYGON = 'moving polygon'
	MOVING_VERTEX = 'moving vertex'
	CREATING_NEW_POLYGON = 'create new polygon'
	ADDING_VERTICES_CONSECUTIVELY = 'adding vertices consecutively'
	ADDING_VERTICES_RANDOMLY = 'adding vertices randomly'
	KEEP_SELECTION = 'keep selection'
	SELECT_UNCERTAIN_SEGMENT = 'select uncertain segment'
	DELETE_ROI_MERGE = 'delete roi (merge)'
	DELETE_ROI_DUPLICATE = 'delete roi (duplicate)'
	DELETE_BETWEEN = 'delete edges between two vertices'
	CONNECT_VERTICES = 'connect two vertices'

class ProposalType(Enum):
	GLOBAL = 'global'
	LOCAL = 'local'
	FREEFORM = 'freeform'
	ALGORITHM = 'algorithm'

class PolygonType(Enum):
	CLOSED = 'closed'
	OPEN = 'open'
	TEXTURE = 'textured'
	TEXTURE_WITH_CONTOUR = 'texture with contour'
	DIRECTION = 'directionality'

SELECTED_POLYGON_LINEWIDTH = 5
UNSELECTED_POLYGON_LINEWIDTH = 3
SELECTED_CIRCLE_SIZE = 30
UNSELECTED_CIRCLE_SIZE = 5
CIRCLE_PICK_THRESH = 1000.
PAN_THRESHOLD = 10

HISTORY_LEN = 20

AUTO_EXTEND_VIEW_TOLERANCE = 200

# NUM_NEIGHBORS_PRELOAD = 1 # preload neighbor sections before and after this number
VERTEX_CIRCLE_RADIUS = 10
	
class BrainLabelingGUI(QMainWindow, Ui_BrainLabelingGui):
	def __init__(self, parent=None, stack=None):
		"""
		Initialization of BrainLabelingGUI.
		"""

		self.params_dir = '../params'

		# self.app = QApplication(sys.argv)
		QMainWindow.__init__(self, parent)

		# self.init_data(stack)
		self.stack = stack

		self.recent_labels = []

		# self.history = deque(maxlen=HISTORY_LEN)

		deque(maxlen=HISTORY_LEN)

		self.history_allSections = defaultdict(list)

		self.new_labelnames = {}
		if os.path.exists(os.environ['REPO_DIR']+'/visualization/newStructureNames.txt'):
			with open(os.environ['REPO_DIR']+'/visualization/newStructureNames.txt', 'r') as f:
				for ln in f.readlines():
					abbr, fullname = ln.split('\t')
					self.new_labelnames[abbr] = fullname.strip()
			self.new_labelnames = OrderedDict(sorted(self.new_labelnames.items()))

		self.structure_names = {}
		with open(os.environ['REPO_DIR']+'/visualization/structure_names.txt', 'r') as f:
			for ln in f.readlines():
				abbr, fullname = ln.split('\t')
				self.structure_names[abbr] = fullname.strip()
		self.structure_names = OrderedDict(self.new_labelnames.items() + sorted(self.structure_names.items()))

		self.first_sec, self.last_sec = section_range_lookup[self.stack]
		# self.midline_sec = midline_section_lookup[self.stack]
		self.midline_sec = (self.first_sec + self.last_sec)/2

		self.red_pen = QPen(Qt.red)
		self.red_pen.setWidth(20)
		self.blue_pen = QPen(Qt.blue)
		self.blue_pen.setWidth(20)
		self.green_pen = QPen(Qt.green)
		self.green_pen.setWidth(20)

		self.initialize_brain_labeling_gui()
		# self.labeling_painters = {}

		self.gscenes = {} # exactly one for each section {section: gscene}
		self.gviews = [self.section1_gview, self.section2_gview, self.section3_gview] # exactly one for each section {section: gscene}

		self.accepted_proposals_allSections = {}

		self.lateral_position_lookup = dict(zip(range(self.first_sec, self.midline_sec+1), -np.linspace(2.64, 0, self.midline_sec-self.first_sec+1)) + \
											zip(range(self.midline_sec, self.last_sec+1), np.linspace(0, 2.64, self.last_sec-self.midline_sec+1)))

	def load_active_set(self, sections=None):

		if sections is None:
			self.sections = [self.section, self.section2, self.section3]
		else:

			minsec = min(sections)
			maxsec = max(sections)

			self.sections = range(max(self.first_sec, minsec), min(self.last_sec, maxsec+1))
			
			print self.sections

			self.dms = dict([(i, DataManager(
			    data_dir=os.environ['DATA_DIR'], 
			         repo_dir=os.environ['REPO_DIR'], 
			         result_dir=os.environ['RESULT_DIR'], 
			         # labeling_dir=os.environ['LOCAL_LABELING_DIR'],
			         labeling_dir='/home/yuncong/CSHL_data_labelings_losslessAlignCropped',
			    stack=stack, section=i, segm_params_id='tSLIC200', load_mask=False)) 
			for i in self.sections])
				# for i in range(self.first_sec, self.last_sec+1)])

			t = time.time()

			if hasattr(self, 'pixmaps'):
				for i in self.sections:
					if i not in self.pixmaps:
						print 'new load', i
						self.pixmaps[i] = QPixmap(self.dms[i]._get_image_filepath(version='rgb-jpg'))
						# self.pixmaps[i] = QPixmap(self.dms[i]._get_image_filepath(version='stereotactic-rgb-jpg'))

				to_remove = []
				for i in self.pixmaps:
					if i not in self.sections:
						print 'pop', i
						to_remove.append(i)
				
				for i in to_remove:
					del self.pixmaps[i]
					# self.pixmaps.pop(i)
					if i in self.gscenes:
						del self.gscenes[i]
					# self.gscenes.pop(i)
			else:	
			
				self.pixmaps = dict([(i, QPixmap(self.dms[i]._get_image_filepath(version='rgb-jpg'))) for i in self.sections])
				# self.pixmaps = dict([(i, QPixmap(self.dms[i]._get_image_filepath(version='stereotactic-rgb-jpg'))) for i in self.sections])
			

			print 'load image', time.time() - t


	def paint_panel(self, panel_id, sec, labeling_username=None):

		# if not hasattr(self, 'grid_pixmap'):
		# 	self.grid_pixmap = QPixmap('/home/yuncong/CSHL_data_processed/MD594_lossless_aligned_cropped_stereotacticGrids.png')

		gview = self.gviews[panel_id]

		if sec in self.gscenes:
			print 'gscene exists'
			gscene = self.gscenes[sec]
		else:
			print 'new gscene'
			pixmap = self.pixmaps[sec]
			gscene = QGraphicsScene(gview)
			gscene.addPixmap(pixmap)
			# gscene.addPixmap(self.grid_pixmap)

			self.accepted_proposals_allSections[sec] = {}

			gscene.update(0, 0, gview.width(), gview.height())
			gscene.keyPressEvent = self.key_pressed

			gscene.installEventFilter(self)

			self.gscenes[sec] = gscene

			# if labeling_username is None and hasattr(self, 'username_toLoad') and self.username_toLoad is not None:
			# 	labeling_username = self.username_toLoad

			# try:
			# 	usr, ts, suffix, result = self.dms[sec].load_proposal_review_result(labeling_username, 'latest', 'consolidated')
			# 	self.load_labelings(result, gscene, sec)
			# except:
			# 	sys.stderr.write('Labeling from %s does not exist for section %d' % (labeling_username, sec))

			try:
				usr, ts, suffix, result = self.dms[sec].load_proposal_review_result(None, 'latest', 'consolidated')
				self.load_labelings(result, gscene, sec)
			except:
				sys.stderr.write('There is no labeling for section %d\n' % sec)

		if panel_id == 0:
			self.section1_gscene = gscene
		elif panel_id == 1:
			self.section2_gscene = gscene
		elif panel_id == 2:
			self.section3_gscene = gscene

		gview.setScene(gscene)

		gview.show()

		# self.section1_gview.setInteractive(True)
		# self.section1_gview.setDragMode(QGraphicsView.RubberBandDrag)
		# self.section1_gview.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
		
		# self.labeling_painters[panel_id] = LabelingPainter(gview, gscene, pixmap)

	# def reload_brain_labeling_gui(self):

	def initialize_brain_labeling_gui(self):

		self.colors = np.loadtxt('100colors.txt', skiprows=1)
		self.label_cmap = ListedColormap(self.colors, name='label_cmap')

		self.setupUi(self)

		self.button_autoDetect.clicked.connect(self.autoDetect_callback)
		# self.button_updateDB.clicked.connect(self.updateDB_callback)
		# self.button_loadLabeling.clicked.connect(self.load_callback)
		self.button_saveLabeling.clicked.connect(self.save_callback)
		self.button_quit.clicked.connect(self.close)

		self.lineEdit_username.returnPressed.connect(self.username_changed)

		self.button_loadLabelingSec1.clicked.connect(self.load_callback1)
		self.button_loadLabelingSec2.clicked.connect(self.load_callback2)
		self.button_loadLabelingSec3.clicked.connect(self.load_callback3)

		# self.display_buttons = [self.img_radioButton, self.textonmap_radioButton, self.dirmap_radioButton]
		# self.img_radioButton.setChecked(True)

		# for b in self.display_buttons:
		# 	b.toggled.connect(self.display_option_changed)

		self.radioButton_globalProposal.toggled.connect(self.mode_changed)
		self.radioButton_localProposal.toggled.connect(self.mode_changed)

		# self.buttonSpOnOff.clicked.connect(self.display_option_changed)
		self.button_labelsOnOff.clicked.connect(self.toggle_labels)
		self.button_contoursOnOff.clicked.connect(self.toggle_contours)
		self.button_verticesOnOff.clicked.connect(self.toggle_vertices)

		# self.thumbnail_list = QListWidget(parent=self)
		self.thumbnail_list.setIconSize(QSize(200,200))
		self.thumbnail_list.setResizeMode(QListWidget.Adjust)
		# self.thumbnail_list.itemDoubleClicked.connect(self.section_changed)
		self.thumbnail_list.itemDoubleClicked.connect(self.init_section_selected)

		for i in range(self.first_sec, self.last_sec):
			item = QListWidgetItem(QIcon("/home/yuncong/CSHL_data_processed/%(stack)s_thumbnail_aligned_cropped/%(stack)s_%(sec)04d_thumbnail_aligned_cropped.tif"%{'sec':i, 'stack': self.stack}), str(i))
			self.thumbnail_list.addItem(item)

		self.thumbnail_list.resizeEvent = self.thumbnail_list_resized
		self.init_thumbnail_list_width = self.thumbnail_list.width()

		self.section1_gscene = None
		self.section2_gscene = None
		self.section3_gscene = None

	def username_changed(self):
		self.username = str(self.sender().text())
		print 'username changed to', self.username

	def showContextMenu(self, pos):
		myMenu = QMenu(self)
		action_newPolygon = myMenu.addAction("New polygon")
		action_deletePolygon = myMenu.addAction("Delete polygon")
		action_setLabel = myMenu.addAction("Set label")
		action_setUncertain = myMenu.addAction("Set uncertain segment")
		action_deleteROIDup = myMenu.addAction("Delete vertices in ROI (duplicate)")
		action_deleteROIMerge = myMenu.addAction("Delete vertices in ROI (merge)")
		action_deleteBetween = myMenu.addAction("Delete edges between two vertices")
		action_closePolygon = myMenu.addAction("Close polygon")
		action_insertVertex = myMenu.addAction("Insert vertex")
		action_appendVertex = myMenu.addAction("Append vertex")
		action_connectVertex = myMenu.addAction("Connect vertex")
		
		action_doneDrawing = myMenu.addAction("Done drawing")

		selected_action = myMenu.exec_(self.gviews[self.selected_panel_id].viewport().mapToGlobal(pos))
		if selected_action == action_newPolygon:
			print 'new polygon'

			print 'accepted'
			print self.accepted_proposals_allSections[self.selected_section].keys()

			invalid_proposals = []
			for p, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
				p.setEnabled(False)

				if 'vertexCircles' not in props:
					invalid_proposals.append(p)
				else:
					for circ in props['vertexCircles']:
						circ.setEnabled(False)

				if 'labelTextArtist' not in props:
					invalid_proposals.append(p)
				else:
					props['labelTextArtist'].setEnabled(False)

			print 'invalid_proposals', invalid_proposals


			self.close_curr_polygon = False
			self.ignore_click = False

			curr_polygon_path = QPainterPath()
			# curr_polygon_path.setFillRule(Qt.WindingFill)

			self.selected_polygon = QGraphicsPathItemModified(curr_polygon_path, gui=self)

			self.selected_polygon.setZValue(50)
			self.selected_polygon.setPen(self.red_pen)
			self.selected_polygon.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemClipsToShape | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemSendsScenePositionChanges)

			self.selected_polygon.signal_emitter.clicked.connect(self.polygon_pressed)
			self.selected_polygon.signal_emitter.moved.connect(self.polygon_moved)
			self.selected_polygon.signal_emitter.released.connect(self.polygon_released)

			assert self.selected_polygon is not None
			self.accepted_proposals_allSections[self.selected_section][self.selected_polygon] = {'vertexCircles': []}

			self.overlap_with = set([])

			self.gscenes[self.selected_section].addItem(self.selected_polygon)

			self.set_mode(Mode.ADDING_VERTICES_CONSECUTIVELY)

		elif selected_action == action_deletePolygon:
			self.remove_polygon(self.selected_polygon)

		elif selected_action == action_setLabel:
			self.open_label_selection_dialog()

		elif selected_action == action_setUncertain:
			self.set_mode(Mode.SELECT_UNCERTAIN_SEGMENT)

		elif selected_action == action_deleteROIDup:
			self.set_mode(Mode.DELETE_ROI_DUPLICATE)
		
		elif selected_action == action_deleteROIMerge:
			self.set_mode(Mode.DELETE_ROI_MERGE)

		elif selected_action == action_deleteBetween:
			self.set_mode(Mode.DELETE_BETWEEN)

		elif selected_action == action_closePolygon:
			new_path = self.selected_polygon.path()
			new_path.closeSubpath()
			self.selected_polygon.setPath(new_path)

		elif selected_action == action_insertVertex:
			self.set_mode(Mode.ADDING_VERTICES_RANDOMLY)

		elif selected_action == action_appendVertex:
			if self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'].index(self.selected_vertex) == 0:
				self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'] = self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'][::-1]
				reversed_path = self.selected_polygon.path().toReversed()
				self.selected_polygon.setPath(reversed_path)

			self.set_mode(Mode.ADDING_VERTICES_CONSECUTIVELY)

		elif selected_action == action_connectVertex:
			self.set_mode(Mode.CONNECT_VERTICES)

		elif selected_action == action_doneDrawing:
			self.set_mode(Mode.IDLE)
			# self.selected_polygon = None

	def add_vertex(self, polygon, x, y, new_index=-1):
		
		ellipse = QGraphicsEllipseItemModified(-VERTEX_CIRCLE_RADIUS, -VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS, gui=self)
		ellipse.setPos(x,y)

		for p in self.accepted_proposals_allSections[self.selected_section]:
			if p != self.selected_polygon:
				if p.path().contains(QPointF(x,y)) or p.path().intersects(polygon.path()):
					print 'overlap_with', self.overlap_with
					self.overlap_with.add(p)

		ellipse.setPen(Qt.blue)
		ellipse.setBrush(Qt.blue)

		ellipse.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemClipsToShape | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemSendsScenePositionChanges)
		ellipse.signal_emitter.moved.connect(self.vertex_moved)
		ellipse.signal_emitter.clicked.connect(self.vertex_clicked)
		ellipse.signal_emitter.released.connect(self.vertex_released)

		self.gscenes[self.selected_section].addItem(ellipse)

		ellipse.setZValue(99)

		if new_index == -1:
			self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles'].append(ellipse)
		else:
			self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles'] = self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles'][:new_index] + \
																[ellipse] + self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles'][new_index:]

		# self.auto_extend_view(x, y)


	@pyqtSlot()
	def polygon_pressed(self):
		self.polygon_is_moved = False

		print [p.zValue() for p in self.accepted_proposals_allSections[self.selected_section]]

	@pyqtSlot(int, int, int, int)
	def polygon_moved(self, x, y, x0, y0):

		offset_scene_x = x - x0
		offset_scene_y = y - y0

		self.selected_polygon = self.sender().parent

		print self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles']

		for i, circ in enumerate(self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles']):
			elem = self.selected_polygon.path().elementAt(i)
			scene_pt = self.selected_polygon.mapToScene(elem.x, elem.y)
			circ.setPos(scene_pt)

		self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['labelTextArtist'].setPos(self.selected_polygon.label_pos_before_move_x + offset_scene_x, 
										self.selected_polygon.label_pos_before_move_y + offset_scene_y)

		self.polygon_is_moved = True
			
	@pyqtSlot()
	def polygon_released(self):
		
		self.selected_polygon = self.sender().parent

		curr_polygon_path = self.selected_polygon.path()

		for i in range(curr_polygon_path.elementCount()):
			elem = curr_polygon_path.elementAt(i)
			scene_pt = self.selected_polygon.mapToScene(elem.x, elem.y)
			
			curr_polygon_path.setElementPositionAt(i, scene_pt.x(), scene_pt.y())
		
		self.selected_polygon.setPath(curr_polygon_path)
		self.selected_polygon.setPos(0,0)

		if self.mode == Mode.MOVING_VERTEX and self.polygon_is_moved:
			self.history_allSections[self.selected_section].append({'type': 'drag_polygon', 'polygon': self.selected_polygon, 'mouse_moved': (self.selected_polygon.release_scene_x - self.selected_polygon.press_scene_x, \
																										self.selected_polygon.release_scene_y - self.selected_polygon.press_scene_y)})
			self.polygon_is_moved = False

			print 'history:', [h['type'] for h in self.history_allSections[self.selected_section]]

				
	@pyqtSlot(int, int, int, int)
	def vertex_moved(self, x, y, x0, y0):

		offset_scene_x = x - x0
		offset_scene_y = y - y0

		self.selected_vertex_circle = self.sender().parent
		
		self.selected_vertex_center_x_new = self.selected_vertex_circle.center_scene_x_before_move + offset_scene_x
		self.selected_vertex_center_y_new = self.selected_vertex_circle.center_scene_y_before_move + offset_scene_y

		for p, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
			if self.selected_vertex_circle in props['vertexCircles']:
				self.selected_polygon = p
				break

		vertex_index = self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'].index(self.selected_vertex_circle)
		# print 'vertex_index', vertex_index

		curr_polygon_path = self.selected_polygon.path()

		if vertex_index == 0 and self.is_path_closed(curr_polygon_path): # closed

			# print self.selected_vertex_center_x_new, self.selected_vertex_center_y_new

			curr_polygon_path.setElementPositionAt(0, self.selected_vertex_center_x_new, self.selected_vertex_center_y_new)
			curr_polygon_path.setElementPositionAt(len(self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles']), \
											self.selected_vertex_center_x_new, self.selected_vertex_center_y_new)

		else:
			curr_polygon_path.setElementPositionAt(vertex_index, self.selected_vertex_center_x_new, self.selected_vertex_center_y_new)

		self.selected_polygon.setPath(curr_polygon_path)

		self.vertex_is_moved = True


	@pyqtSlot()
	def vertex_clicked(self):
		print self.sender().parent, 'clicked'

		self.vertex_is_moved = False

		clicked_vertex = self.sender().parent

		for p, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
			if clicked_vertex in props['vertexCircles']:
				self.selected_polygon = p
				break

		assert clicked_vertex in self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles']

		if self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'].index(clicked_vertex) == 0 and \
			len(self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles']) > 2 and \
			self.mode == Mode.ADDING_VERTICES_CONSECUTIVELY:
			print 'close curr polygon SET'
			self.close_curr_polygon = True

		else:
			self.ignore_click = True

	@pyqtSlot()
	def vertex_released(self):
		print self.sender().parent, 'released'

		clicked_vertex = self.sender().parent

		if self.mode == Mode.MOVING_VERTEX and self.vertex_is_moved:
			self.history_allSections[self.selected_section].append({'type': 'drag_vertex', 'polygon': self.selected_polygon, 'vertex': clicked_vertex, \
								 'mouse_moved': (clicked_vertex.release_scene_x - clicked_vertex.press_scene_x, \
								 	clicked_vertex.release_scene_y - clicked_vertex.press_scene_y)})

			self.vertex_is_moved = False

			print 'history:', [h['type'] for h in self.history_allSections[self.selected_section]]

		elif self.mode == Mode.DELETE_BETWEEN:
			vertex_index = self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'].index(clicked_vertex)
			print 'vertex_index', vertex_index 

			rect = clicked_vertex.rect()
			clicked_vertex.setRect(rect.x()-.5*VERTEX_CIRCLE_RADIUS, rect.y()-.5*VERTEX_CIRCLE_RADIUS, 3*VERTEX_CIRCLE_RADIUS, 3*VERTEX_CIRCLE_RADIUS)

			if hasattr(self, 'first_vertex_index_to_delete') and self.first_vertex_index_to_delete is not None:
				self.second_vertex_index_to_delete = vertex_index

				self.delete_between(self.selected_polygon, self.first_vertex_index_to_delete, self.second_vertex_index_to_delete)
				
				# first_vertex = self.accepted_proposals[self.selected_polygon]['vertexCircles'][self.first_vertex_index_to_delete]
				# rect = first_vertex.rect()
				# first_vertex.setRect(rect.x()-50, rect.y()-50, 100, 100)
				
				self.first_vertex_index_to_delete = None

				# second_vertex = self.accepted_proposals[self.selected_polygon]['vertexCircles'][self.second_vertex_index_to_delete]
				# rect = second_vertex.rect()
				# second_vertex.setRect(rect.x()-50, rect.y()-50, 100, 100)

				self.second_vertex_index_to_delete = None

				self.set_mode(Mode.IDLE)

			else:
				self.first_vertex_index_to_delete = vertex_index

		elif self.mode == Mode.CONNECT_VERTICES:
			vertex_index = self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'].index(clicked_vertex)

			print 'vertex_index', vertex_index 

			rect = clicked_vertex.rect()
			clicked_vertex.setRect(rect.x()-.5*VERTEX_CIRCLE_RADIUS, rect.y()-.5*VERTEX_CIRCLE_RADIUS, 3*VERTEX_CIRCLE_RADIUS, 3*VERTEX_CIRCLE_RADIUS)

			if hasattr(self, 'first_vertex_index_to_connect') and self.first_vertex_index_to_connect is not None:
				self.second_polygon = self.selected_polygon
				self.second_vertex_index_to_connect = vertex_index

				self.connect_vertices(self.first_polygon, self.first_vertex_index_to_connect, self.second_polygon, self.second_vertex_index_to_connect)
				
				if self.first_polygon == self.second_polygon: # not creating new polygon, so need to restore the vertex circle sizes

					first_vertex = self.accepted_proposals_allSections[self.selected_section][self.first_polygon]['vertexCircles'][self.first_vertex_index_to_connect]
					rect = first_vertex.rect()
					first_vertex.setRect(rect.x()+.5*VERTEX_CIRCLE_RADIUS, rect.y()+.5*VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS)
				
					second_vertex = self.accepted_proposals_allSections[self.selected_section][self.second_polygon]['vertexCircles'][self.second_vertex_index_to_connect]
					rect = second_vertex.rect()
					second_vertex.setRect(rect.x()+.5*VERTEX_CIRCLE_RADIUS, rect.y()+.5*VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS)

				self.first_polygon = None
				self.first_vertex_index_to_connect = None

				self.second_polygon = None
				self.second_vertex_index_to_connect = None

				self.set_mode(Mode.IDLE)

			else:
				self.first_polygon = self.selected_polygon
				self.first_vertex_index_to_connect = vertex_index


	def set_flag_all(self, flag, enabled):

		if hasattr(self, 'accepted_proposals_allSections'):

			for ac in self.accepted_proposals_allSections.itervalues():
				for p, props in ac.iteritems():
					p.setFlag(flag, enabled)
					for circ in props['vertexCircles']:
						circ.setFlag(flag, enabled)
					if 'labelTextArtist' in props:
						props['labelTextArtist'].setFlag(flag, enabled)


	def eventFilter(self, obj, event):

		if event.type() == QEvent.GraphicsSceneMousePress or event.type() == QEvent.GraphicsSceneMouseRelease or event.type() == QEvent.Wheel:

			if obj == self.section1_gscene or obj == self.section1_gview.viewport() :
				self.selected_section = self.section
				self.selected_panel_id = 0
			elif obj == self.section2_gscene or obj == self.section2_gview.viewport() :
				self.selected_section = self.section2
				self.selected_panel_id = 1
			elif obj == self.section3_gscene or obj == self.section3_gview.viewport() :
				self.selected_section = self.section3
				self.selected_panel_id = 2

		# if hasattr(self, 'selected_section'):
		# 	print 'selected_section = ', self.selected_section

		if (obj == self.section1_gview.viewport() or \
			obj == self.section2_gview.viewport() or \
			obj == self.section3_gview.viewport()) and event.type() == QEvent.Wheel:
			self.zoom_scene(event)
			return True

		if obj == self.section1_gscene:
			obj_type = 'gscene'
			gscene = self.section1_gscene
			gview = self.section1_gview
			# self.selected_section = self.section
			# self.selected_panel_id = 0
		elif obj == self.section2_gscene:
			obj_type = 'gscene'
			gscene = self.section2_gscene
			gview = self.section2_gview
			# self.selected_section = self.section2
			# self.selected_panel_id = 1
		elif obj == self.section3_gscene:
			obj_type = 'gscene'
			gscene = self.section3_gscene
			gview = self.section3_gview
			# self.selected_section = self.section3
			# self.selected_panel_id = 2
		else:
			obj_type = 'other'


		# if obj == self.section1_gscene and event.type() == QEvent.GraphicsSceneMousePress:
		if obj_type == 'gscene' and event.type() == QEvent.GraphicsSceneMousePress:

			### with this, single click can select an item; without this only double click can select an item (WEIRD !!!)
			# self.section1_gview.translate(0.1, 0.1)
			# self.section1_gview.translate(-0.1, -0.1)

			gview.translate(0.1, 0.1)
			gview.translate(-0.1, -0.1)
			##########################################

			# print 'enabled', [p.isEnabled() for p, props in self.accepted_proposals_allPanels[section].iteritems()]

			# if self.mode == Mode.MOVING_VERTEX or self.mode == Mode.ADDING_VERTICES_CONSECUTIVELY:
			obj.mousePressEvent(event) # let gscene handle the event (i.e. determine which item or whether an item receives it)

			# print 'close curr polygon', self.close_curr_polygon

			if self.mode == Mode.ADDING_VERTICES_CONSECUTIVELY:

				x = event.scenePos().x()
				y = event.scenePos().y()

				if hasattr(self, 'close_curr_polygon') and self.close_curr_polygon:

					print 'close contour'

					path = self.selected_polygon.path()
					path.closeSubpath()
					self.selected_polygon.setPath(path)

					# self.history.append({'type': 'close_contour', 'polygon': self.selected_polygon})
					self.history_allSections[self.selected_section].append({'type': 'add_vertex', 'polygon': self.selected_polygon, 'index': len(self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'])-1})
					print 'history:', [h['type'] for h in self.history_allSections[self.selected_section]]

				elif self.ignore_click:
					self.ignore_click = False

				else:

					self.add_vertex(self.selected_polygon, x, y)

					path = self.selected_polygon.path()

					if path.elementCount() == 0:
						path.moveTo(x,y)
					else:
						path.lineTo(x,y)

					self.selected_polygon.setPath(path)

					self.history_allSections[self.selected_section].append({'type': 'add_vertex', 'polygon': self.selected_polygon, 'index': len(self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'])-1})
					print 'history:', [h['type'] for h in self.history_allSections[self.selected_section]]

				return True

			elif self.mode == Mode.ADDING_VERTICES_RANDOMLY:

				x = event.scenePos().x()
				y = event.scenePos().y()

				new_index = self.find_vertex_insert_position(self.selected_polygon, x, y)
				print 'new index', new_index

				path = self.selected_polygon.path()
				new_path = QPainterPath()
				for i in range(path.elementCount()):
					if i == new_index:
						new_path.lineTo(x, y)
					elem = path.elementAt(i)
					if new_path.elementCount() == 0:
						new_path.moveTo(elem.x, elem.y)
					else:
						new_path.lineTo(elem.x, elem.y)
				self.selected_polygon.setPath(new_path)

				self.add_vertex(self.selected_polygon, x, y, new_index)

				self.history_allSections[self.selected_section].append({'type': 'add_vertex', 'polygon': self.selected_polygon, 'index': new_index})
				print 'history:', [h['type'] for h in self.history_allSections[self.selected_section]]

				return True

			elif self.mode == Mode.IDLE:

				self.press_x = event.pos().x()
				self.press_y = event.pos().y()

				self.press_screen_x = event.screenPos().x()
				self.press_screen_y = event.screenPos().y()

				self.pressed = True

				return True
				
			return False

		# if obj == self.section1_gscene and event.type() == QEvent.GraphicsSceneMouseMove:
		if obj_type == 'gscene' and event.type() == QEvent.GraphicsSceneMouseMove:

			# print 'event filter: mouse move'

			if self.mode == Mode.MOVING_VERTEX:
				obj.mouseMoveEvent(event)

			elif self.mode == Mode.IDLE:
				if hasattr(self, 'event_caused_by_panning') and self.event_caused_by_panning:
					# self.event_caused_by_panning = False
					return True

				if self.pressed:

					self.event_caused_by_panning = True

					self.curr_scene_x = event.scenePos().x()
					self.curr_scene_y = event.scenePos().y()

					self.last_scene_x = event.lastScenePos().x()
					self.last_scene_y = event.lastScenePos().y()

					self.section1_gview.translate(self.curr_scene_x - self.last_scene_x, self.curr_scene_y - self.last_scene_y)
					self.section2_gview.translate(self.curr_scene_x - self.last_scene_x, self.curr_scene_y - self.last_scene_y)
					self.section3_gview.translate(self.curr_scene_x - self.last_scene_x, self.curr_scene_y - self.last_scene_y)
					# these move canvas and trigger GraphicsSceneMouseMove event again, causing recursion

					print self.curr_scene_x - self.last_scene_x, self.curr_scene_y - self.last_scene_y

					self.event_caused_by_panning = False
					return True

			return False



		# if obj == self.section1_gscene and event.type() == QEvent.GraphicsSceneMouseRelease:
		if obj_type == 'gscene' and event.type() == QEvent.GraphicsSceneMouseRelease:

			obj.mouseReleaseEvent(event)

			if self.mode == Mode.MOVING_VERTEX or self.mode == Mode.ADDING_VERTICES_CONSECUTIVELY:

				if hasattr(self, 'close_curr_polygon') and self.close_curr_polygon:

					print 'close curr polygon UNSET'
					self.close_curr_polygon = False
					
					self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['subtype'] = PolygonType.CLOSED
					self.complete_polygon()

					# self.selected_polygon = None

					self.set_mode(Mode.IDLE)

			elif self.mode == Mode.IDLE:
				self.release_scene_x = event.scenePos().x()
				self.release_scene_y = event.scenePos().y()

				self.pressed = False

				return True

			elif self.mode == Mode.DELETE_ROI_MERGE or self.mode == Mode.DELETE_ROI_DUPLICATE:

				selected_polygons = self.analyze_rubberband_selection()

				for p, vs in selected_polygons.iteritems():
					print p, vs
					if self.mode == Mode.DELETE_ROI_DUPLICATE:
						self.delete_vertices(p, vs)
					elif self.mode == Mode.DELETE_ROI_MERGE:
						self.delete_vertices(p, vs, merge=True)

				self.set_mode(Mode.IDLE)

			elif self.mode == Mode.SELECT_UNCERTAIN_SEGMENT:

				selected_polygons = self.analyze_rubberband_selection()

				for polygon, vertex_indices in selected_polygons.iteritems():
					self.set_uncertain(polygon, vertex_indices)

				self.set_mode(Mode.IDLE)

			return False

		return False


	def analyze_rubberband_selection(self):

		items = self.gscenes[self.selected_section].selectedItems()

		vertices_selected = [i for i in items if i not in self.accepted_proposals_allSections[self.selected_section] and isinstance(i, QGraphicsEllipseItemModified)]

		polygons = defaultdict(list)
		for v in vertices_selected:
			for p, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
				if v in props['vertexCircles']:
					polygons[p].append(props['vertexCircles'].index(v))

		return polygons # {polygon: vertex_indices}


	def subpath(self, path, begin, end):

		new_path = QPainterPath()

		is_closed = self.is_path_closed(path)
		n = path.elementCount() - 1 if is_closed else path.elementCount()

		if not is_closed:
			assert end >= begin
			begin = max(0, begin)
			end = min(n-1, end)
		else:
			assert end != begin # cannot handle this, because there is no way a path can have the same first and last points but is not closed
			if end < begin: 
				end = end + n

		for i in range(begin, end + 1):
			elem = path.elementAt(i % n)
			if new_path.elementCount() == 0:
				new_path.moveTo(elem.x, elem.y)
			else:
				new_path.lineTo(elem.x, elem.y)

		assert new_path.elementCount() > 0

		return new_path

	def set_uncertain(self, polygon, vertex_indices):

		uncertain_paths, certain_paths = self.split_path(polygon.path(), vertex_indices)

		for path in uncertain_paths:
			new_uncertain_polygon = self.add_polygon_vertices_label(path, self.green_pen, self.accepted_proposals_allSections[self.selected_section][polygon]['label'])

		for path in certain_paths:
			new_certain_polygon = self.add_polygon_vertices_label(path, self.red_pen, self.accepted_proposals_allSections[self.selected_section][polygon]['label'])

		self.remove_polygon(polygon)

		# self.history.append({'type': 'set_uncertain_segment', 'old_polygon': polygon, 'new_certain_polygon': new_certain_polygon, 'new_uncertain_polygon': new_uncertain_polygon})
		# print 'history:', [h['type'] for h in self.history]


	def connect_vertices(self, polygon1, vertex_ind1, polygon2, vertex_ind2):
		if polygon1 == polygon2:
			path = polygon1.path()
			is_closed = self.is_path_closed(path)
			n = path.elementCount() -1 if is_closed else path.elementCount()
			assert (vertex_ind1 == 0 and vertex_ind2 == n-1) or (vertex_ind1 == n-1 and vertex_ind2 == 0)
			path.closeSubpath()
			polygon1.setPath(path)

		else:
			path1 = polygon1.path()
			is_closed = self.is_path_closed(path1)
			n1 = path1.elementCount() -1 if is_closed else path1.elementCount()

			path2 = polygon2.path()
			is_closed2 = self.is_path_closed(path2)
			n2 = path2.elementCount() -1 if is_closed2 else path2.elementCount()

			assert not is_closed and not is_closed2 and vertex_ind1 in [0, n1-1] and vertex_ind2 in [0, n2-1]

			if vertex_ind1 == 0 and vertex_ind2 == 0:
				reversed_path1 = path1.toReversed()
				for i in range(path2.elementCount()):
					elem = path2.elementAt(i) 
					reversed_path1.lineTo(elem.x, elem.y)
				new_polygon = self.add_polygon_vertices_label(reversed_path1, pen=self.red_pen, label=self.accepted_proposals_allSections[self.selected_section][polygon1]['label'])
				
			elif vertex_ind1 == n1-1 and vertex_ind2 == n2-1:

				reversed_path2 = path2.toReversed()
				for i in range(reversed_path2.elementCount()):
					elem = reversed_path2.elementAt(i)
					path1.lineTo(elem.x, elem.y)
				new_polygon = self.add_polygon_vertices_label(path1, pen=self.red_pen, label=self.accepted_proposals_allSections[self.selected_section][polygon1]['label'])
				
			elif vertex_ind1 == 0 and vertex_ind2 == n2-1:
				for i in range(path1.elementCount()):
					elem = path1.elementAt(i)
					path2.lineTo(elem.x, elem.y)
				new_polygon = self.add_polygon_vertices_label(path2, pen=self.red_pen, label=self.accepted_proposals_allSections[self.selected_section][polygon1]['label'])

			elif vertex_ind1 == n1-1 and vertex_ind2 == 0:
				for i in range(path2.elementCount()):
					elem = path2.elementAt(i)
					path1.lineTo(elem.x, elem.y)
				new_polygon = self.add_polygon_vertices_label(path1, pen=self.red_pen, label=self.accepted_proposals_allSections[self.selected_section][polygon1]['label'])

			self.remove_polygon(polygon1)
			self.remove_polygon(polygon2)


	def remove_polygon(self, polygon):
		for circ in self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles']:
			self.gscenes[self.selected_section].removeItem(circ)

		if 'labelTextArtist' in self.accepted_proposals_allSections[self.selected_section][polygon]:
			self.gscenes[self.selected_section].removeItem(self.accepted_proposals_allSections[self.selected_section][polygon]['labelTextArtist'])

		self.gscenes[self.selected_section].removeItem(polygon)

		self.accepted_proposals_allSections[self.selected_section].pop(polygon)


	def add_polygon(self, path, pen, z_value=50, uncertain=False):

		polygon = QGraphicsPathItemModified(path, gui=self)
		polygon.setPen(pen)

		polygon.setZValue(z_value)
		polygon.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemClipsToShape | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemSendsScenePositionChanges)

		polygon.signal_emitter.clicked.connect(self.polygon_pressed)
		polygon.signal_emitter.moved.connect(self.polygon_moved)
		polygon.signal_emitter.released.connect(self.polygon_released)

		self.gscenes[self.selected_section].addItem(polygon)

		self.accepted_proposals_allSections[self.selected_section][polygon] = {'vertexCircles': [], 'uncertain': uncertain}

		return polygon


	def add_label_to_polygon(self, polygon, label, label_pos=None):

		self.accepted_proposals_allSections[self.selected_section][polygon]['label'] = label

		textItem = QGraphicsSimpleTextItem(QString(label))

		if label_pos is None:
			centroid = np.mean([(v.scenePos().x(), v.scenePos().y()) for v in self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles']], axis=0)
			textItem.setPos(centroid[0], centroid[1])
		else:
			textItem.setPos(label_pos[0], label_pos[1])

		textItem.setScale(1.5)

		textItem.setFlags(QGraphicsItem.ItemIgnoresTransformations | QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemClipsToShape | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemSendsScenePositionChanges)

		textItem.setZValue(99)
		self.accepted_proposals_allSections[self.selected_section][polygon]['labelTextArtist'] = textItem

		self.gscenes[self.selected_section].addItem(textItem)


	def add_vertices_to_polygon(self, polygon):

		if polygon not in self.accepted_proposals_allSections[self.selected_section]:
			self.accepted_proposals_allSections[self.selected_section][polygon] = {'vertexCircles': []}

		path = polygon.path()
		elem_first = path.elementAt(0)
		elem_last = path.elementAt(path.elementCount()-1)
		is_closed = (elem_first.x == elem_last.x) & (elem_first.y == elem_last.y)

		# if is_closed:
		# 	self.accepted_proposals[polygon]['subtype'] = PolygonType.CLOSED
		# else:
		# 	self.accepted_proposals[polygon]['subtype'] = PolygonType.OPEN

		n = path.elementCount() - 1 if is_closed else path.elementCount()
		print n

		overlap_polygons = set([])

		for i in range(n):

			ellipse = QGraphicsEllipseItemModified(-VERTEX_CIRCLE_RADIUS, -VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS, gui=self)

 			elem = path.elementAt(i)

			ellipse.setPos(elem.x, elem.y)

			for p in self.accepted_proposals_allSections[self.selected_section]:
				if p != polygon:
					if p.path().contains(QPointF(elem.x, elem.y)) or p.path().intersects(polygon.path()):
						print 'overlap_with', overlap_polygons
						overlap_polygons.add(p)

			ellipse.setPen(Qt.blue)
			ellipse.setBrush(Qt.blue)

			ellipse.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemClipsToShape | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemSendsScenePositionChanges)
			ellipse.signal_emitter.moved.connect(self.vertex_moved)
			ellipse.signal_emitter.clicked.connect(self.vertex_clicked)
			ellipse.signal_emitter.released.connect(self.vertex_released)

			self.gscenes[self.selected_section].addItem(ellipse)

			ellipse.setZValue(99)

			self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles'].append(ellipse)

		return overlap_polygons


	def restack_polygons(self, polygon, overlapping_polygons):

		for p in overlapping_polygons:
			if p.path().contains(polygon.path()): # new polygon within existing polygon, it must has higher z value
				new_z = max(polygon.zValue(), p.zValue()+1)
				print polygon, '=>', new_z
				polygon.setZValue(new_z)

			elif polygon.path().contains(p.path()):  # new polygon wraps existing polygon, it must has lower z value
				new_z = min(polygon.zValue(), p.zValue()-1)
				print polygon, '=>', new_z
				polygon.setZValue(new_z)


	def complete_polygon(self):

		self.open_label_selection_dialog()

		self.restack_polygons(self.selected_polygon, self.overlap_with)

		print 'accepted', self.accepted_proposals_allSections[self.selected_section].keys()

		for p, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
			p.setEnabled(True)
			for circ in props['vertexCircles']:
				circ.setEnabled(True)
			if 'labelTextArtist' in props:
				props['labelTextArtist'].setEnabled(True)

		self.save_selected_section(self.selected_section)

	def zoom_scene(self, event):

		pos = self.gviews[self.selected_panel_id].mapToScene(event.pos())

		out_factor = .9
		in_factor = 1./out_factor
		
		if event.delta() < 0: # negative means towards user

			offset_x = (1 - out_factor) * pos.x()
			offset_y = (1 - out_factor) * pos.y()

			# self.section1_gview.translate(-pos.x(), -pos.y())
			self.section1_gview.scale(out_factor, out_factor)
			self.section1_gview.translate(in_factor * offset_x, in_factor * offset_y)

			# self.section2_gview.translate(pos.x(), pos.y())
			self.section2_gview.scale(out_factor, out_factor)
			self.section2_gview.translate(in_factor * offset_x, in_factor * offset_y)
			# self.section2_gview.translate(-pos.x(), -pos.y())

			self.section3_gview.scale(out_factor, out_factor)
			self.section3_gview.translate(in_factor * offset_x, in_factor * offset_y)

		else:
			offset_x = (in_factor - 1) * pos.x()
			offset_y = (in_factor - 1) * pos.y()

			# self.section1_gview.translate(-pos.x(), -pos.y())
			self.section1_gview.scale(in_factor, in_factor)
			self.section1_gview.translate(-out_factor * offset_x, -out_factor * offset_y)
			# self.section1_gview.translate(pos.x(), pos.y())

			# self.section2_gview.translate(pos.x(), pos.y())
			self.section2_gview.scale(in_factor, in_factor)
			self.section2_gview.translate(-out_factor * offset_x, -out_factor * offset_y)
			# self.section2_gview.translate(-pos.x(), -pos.y())

			self.section3_gview.scale(in_factor, in_factor)
			self.section3_gview.translate(-out_factor * offset_x, -out_factor * offset_y)

	def key_pressed(self, event):

		if event.key() == Qt.Key_Left:
			# print 'left'
			self.section1_gview.translate(200, 0)
			self.section2_gview.translate(200, 0)
			self.section3_gview.translate(200, 0)

		elif event.key() == Qt.Key_Right:
			# print 'right'
			self.section1_gview.translate(-200, 0)
			self.section2_gview.translate(-200, 0)
			self.section3_gview.translate(-200, 0)

		elif event.key() == Qt.Key_Up:
			# print 'up'
			self.section1_gview.translate(0, 200)
			self.section2_gview.translate(0, 200)
			self.section3_gview.translate(0, 200)
			
		elif event.key() == Qt.Key_Down:
			# print 'down'
			self.section1_gview.translate(0, -200)
			self.section2_gview.translate(0, -200)
			self.section3_gview.translate(0, -200)

		elif event.key() == Qt.Key_Equal:
			pos = self.gviews[self.selected_panel_id].mapToScene(self.gviews[self.selected_panel_id].mapFromGlobal(QCursor.pos()))
			
			out_factor = .9
			in_factor = 1./out_factor

			offset_x = (1-out_factor) * pos.x()
			offset_y = (1-out_factor) * pos.y()
		
			self.section1_gview.scale(out_factor, out_factor)
			self.section1_gview.translate(in_factor * offset_x, in_factor * offset_y)

			self.section2_gview.scale(out_factor, out_factor)
			self.section2_gview.translate(in_factor * offset_x, in_factor * offset_y)

			self.section3_gview.scale(out_factor, out_factor)
			self.section3_gview.translate(in_factor * offset_x, in_factor * offset_y)

		elif event.key() == Qt.Key_Minus:
			pos = self.gviews[self.selected_panel_id].mapToScene(self.gviews[self.selected_panel_id].mapFromGlobal(QCursor.pos()))

			out_factor = .9
			in_factor = 1./out_factor

			offset_x = (in_factor - 1) * pos.x()
			offset_y = (in_factor - 1) * pos.y()
			
			self.section1_gview.scale(in_factor, in_factor)
			self.section1_gview.translate(-out_factor * offset_x, -out_factor * offset_y)

			self.section2_gview.scale(in_factor, in_factor)
			self.section2_gview.translate(-out_factor * offset_x, -out_factor * offset_y)

			self.section3_gview.scale(in_factor, in_factor)
			self.section3_gview.translate(-out_factor * offset_x, -out_factor * offset_y)

		elif event.key() == Qt.Key_Space:
			if self.mode == Mode.IDLE:
				self.set_mode(Mode.MOVING_VERTEX)
			else:
			# elif self.mode == Mode.MOVING_VERTEX:
				self.set_mode(Mode.IDLE)
				# self.selected_polygon = None

		elif event.key() == Qt.Key_Return:
			print 'enter pressed'

			if self.selected_polygon is not None:
				# self.accepted_proposals[self.selected_polygon]['subtype'] = PolygonType.OPEN

				if 'label' not in self.accepted_proposals_allSections[self.selected_section][self.selected_polygon] or self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['label'] == '':
					self.complete_polygon()

			self.set_mode(Mode.IDLE)
			# self.selected_polygon = None

		elif event.key() == Qt.Key_C:
			path = self.selected_polygon.path()
			path.closeSubpath()
			self.selected_polygon.setPath(path)

			self.history_allSections[self.selected_section].append({'type': 'add_vertex', 'polygon': self.selected_polygon, 'index': len(self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles'])-1})
			print 'history:', [h['type'] for h in self.history_allSections[self.selected_section]]

			self.close_curr_polygon = False
					
			# self.accepted_proposals[self.selected_polygon]['subtype'] = PolygonType.CLOSED
			self.complete_polygon()

			# self.selected_polygon = None

			self.set_mode(Mode.IDLE)

		elif event.key() == Qt.Key_Backspace:
			self.undo()

		elif event.key() == Qt.Key_3 or event.key() == Qt.Key_4:

			if event.key() == Qt.Key_3:
				if self.section == self.first_sec or self.section - 1 not in self.sections:
					return
				else:
					self.section = self.section - 1
			else:
				if self.section == self.last_sec or self.section + 1 not in self.sections:
					return
				else:
					self.section = self.section + 1

			self.setWindowTitle('BrainLabelingGUI, stack %s'%self.stack + ', Left %d' %self.section3 + ' (%.3f)' % self.lateral_position_lookup[self.section3] + \
														', middle %d'%self.section + ' (%.3f)' % self.lateral_position_lookup[self.section] + \
														', right %d'%self.section2 + ' (%.3f)' % self.lateral_position_lookup[self.section2])

			self.paint_panel(0, self.section)


		elif event.key() == Qt.Key_5 or event.key() == Qt.Key_6:

			if event.key() == Qt.Key_5:
				if self.section2 == self.first_sec or self.section2 - 1 not in self.sections:
					return
				else:
					self.section2 = self.section2 - 1
			else:
				if self.section2 == self.last_sec or self.section2 + 1 not in self.sections:
					return
				else:
					self.section2 = self.section2 + 1
			
			self.setWindowTitle('BrainLabelingGUI, stack %s'%self.stack + ', Left %d' %self.section3 + ' (%.3f)' % self.lateral_position_lookup[self.section3] + \
											', middle %d'%self.section + ' (%.3f)' % self.lateral_position_lookup[self.section] + \
											', right %d'%self.section2 + ' (%.3f)' % self.lateral_position_lookup[self.section2])

			self.paint_panel(1, self.section2)


		elif event.key() == Qt.Key_1 or event.key() == Qt.Key_2:

			if event.key() == Qt.Key_1:
				if self.section3 == self.first_sec or self.section3 - 1 not in self.sections:
					return
				else:
					self.section3 = self.section3 - 1
			else:
				if self.section3 == self.last_sec or self.section3 + 1 not in self.sections:
					return
				else:
					self.section3 = self.section3 + 1
			
			
			self.setWindowTitle('BrainLabelingGUI, stack %s'%self.stack + ', Left %d' %self.section3 + ' (%.3f)' % self.lateral_position_lookup[self.section3] + \
											', middle %d'%self.section + ' (%.3f)' % self.lateral_position_lookup[self.section] + \
											', right %d'%self.section2 + ' (%.3f)' % self.lateral_position_lookup[self.section2])

			self.paint_panel(2, self.section3)

	##########################

	def thumbnail_list_resized(self, event):
		new_size = 200 * event.size().width() / self.init_thumbnail_list_width
		self.thumbnail_list.setIconSize( QSize(new_size , new_size ) )

	def toggle_labels(self):

		self.labels_on = not self.labels_on

		if not self.labels_on:

			for polygon, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
				props['labelTextArtist'].setVisible(False)

			self.button_labelsOnOff.setText('Turns Labels ON')

		else:
			for polygon, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
				props['labelTextArtist'].setVisible(True)

			self.button_labelsOnOff.setText('Turns Labels OFF')

	def toggle_contours(self):

		self.contours_on = not self.contours_on

		if not self.contours_on:

			for polygon, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
				polygon.setVisible(False)
				if self.vertices_on:
					for circ in props['vertexCircles']:
						circ.setVisible(False)

			self.button_contoursOnOff.setText('Turns Contours ON')

		else:
			for polygon, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
				polygon.setVisible(True)
				if self.vertices_on:
					for circ in props['vertexCircles']:
						circ.setVisible(True)

			self.button_contoursOnOff.setText('Turns Contours OFF')


	def toggle_vertices(self):

		self.vertices_on = not self.vertices_on

		if not self.vertices_on:

			for polygon, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
				for circ in props['vertexCircles']:
					circ.setVisible(False)

			self.button_verticesOnOff.setText('Turns Vertices ON')

		else:
			for polygon, props in self.accepted_proposals_allSections[self.selected_section].iteritems():
				for circ in props['vertexCircles']:
					circ.setVisible(True)

			self.button_verticesOnOff.setText('Turns Vertices OFF')

	# def updateDB_callback(self):
	# 	cmd = 'rsync -az --include="*/" %(local_labeling_dir)s/%(stack)s yuncong@gcn-20-33.sdsc.edu:%(gordon_labeling_dir)s' % {'gordon_labeling_dir':os.environ['GORDON_LABELING_DIR'],
	# 																		'local_labeling_dir':os.environ['LOCAL_LABELING_DIR'],
	# 																		'stack': self.stack
	# 																		}
	# 	os.system(cmd)

	# 	# cmd = 'rsync -az %(local_labeling_dir)s/labelnames.txt yuncong@gcn-20-33.sdsc.edu:%(gordon_labeling_dir)s' % {'gordon_labeling_dir':os.environ['GORDON_LABELING_DIR'],
	# 	#                                                             'local_labeling_dir':os.environ['LOCAL_LABELING_DIR'],
	# 	#                                                             }
	# 	# os.system(cmd)
	# 	self.statusBar().showMessage('labelings synced')

	# 	# payload = {'section': self.dm.slice_ind}
	# 	# r = requests.get('http://gcn-20-32.sdsc.edu:5000/update_db', params=payload)
	# 	r = requests.get('http://gcn-20-32.sdsc.edu:5000/update_db')
	# 	res = r.json()
	# 	if res['result'] == 0:
	# 		self.statusBar().showMessage('Landmark database updated')


	def detect_landmark(self, labels):

		payload = {'labels': labels, 'section': self.dm.slice_ind}
		r = requests.get('http://gcn-20-32.sdsc.edu:5000/top_down_detect', params=payload)
		print r.url
		return r.json()

	def autoDetect_callback(self):
		self.labelsToDetect = ListSelection([abbr + ' (' + fullname + ')' for abbr, fullname in self.structure_names.iteritems()], parent=self)
		self.labelsToDetect.exec_()

		if len(self.labelsToDetect.selected) > 0:
		
			returned_alg_proposal_dict = self.detect_landmark([x.split()[0] for x in list(self.labelsToDetect.selected)]) 
			# list of tuples (sps, dedges, sig)

			for label, (sps, dedges, sig) in returned_alg_proposal_dict.iteritems():

				props = {}

				props['vertices'] = self.dm.vertices_from_dedges(dedges)
				patch = Polygon(props['vertices'], closed=True, edgecolor=self.boundary_colors[0], fill=False, linewidth=UNSELECTED_POLYGON_LINEWIDTH)
				patch.set_picker(True)
				self.axis.add_patch(patch)

				props['vertexPatches'] = []
				for x,y in props['vertices']:
					vertex_circle = plt.Circle((x, y), radius=UNSELECTED_CIRCLE_SIZE, color=self.boundary_colors[1], alpha=.8)
					vertex_circle.set_picker(CIRCLE_PICK_THRESH)
					props['vertexPatches'].append(vertex_circle)
					self.axis.add_patch(vertex_circle)
					vertex_circle.set_picker(True)


				centroid = np.mean(props['vertices'], axis=0)
				props['labelTextArtist'] = Text(centroid[0], centroid[1], label, style='italic', bbox={'facecolor':'white', 'alpha':0.5, 'pad':10})
				self.axis.add_artist(props['labelTextArtist'])
				props['labelTextArtist'].set_picker(True)

				self.accepted_proposals[patch] = props

				props['sps'] = sps
				props['dedges'] = dedges
				props['sig'] = sig
				props['type'] = ProposalType.ALGORITHM
				props['label'] = label
		
		self.canvas.draw()

	def on_pick(self, event):

		self.object_picked = False

		if event.mouseevent.name == 'scroll_event':
			return

		print 'pick callback triggered'

		self.picked_artists.append(event.artist)


	def load_local_proposals(self):

		sys.stderr.write('loading local proposals ...\n')
		self.statusBar().showMessage('loading local proposals ...')
		
		cluster_tuples = self.dm.load_pipeline_result('allSeedClusterScoreDedgeTuples')
		self.local_proposal_tuples = [(cl, ed, sig) for seed, cl, sig, ed in cluster_tuples]
		self.local_proposal_clusters = [m[0] for m in self.local_proposal_tuples]
		self.local_proposal_dedges = [m[1] for m in self.local_proposal_tuples]
		self.local_proposal_sigs = [m[2] for m in self.local_proposal_tuples]

		self.n_local_proposals = len(self.local_proposal_tuples)
		
		if not hasattr(self, 'local_proposal_pathPatches'):
			self.local_proposal_pathPatches = [None] * self.n_local_proposals
			self.local_proposal_vertexCircles = [None] * self.n_local_proposals

		self.local_proposal_indices_from_sp = defaultdict(list)
		for i, (seed, _, _, _) in enumerate(cluster_tuples):
			self.local_proposal_indices_from_sp[seed].append(i)
		self.local_proposal_indices_from_sp.default_factory = None

		sys.stderr.write('%d local proposals loaded.\n' % self.n_local_proposals)
		self.statusBar().showMessage('Local proposals loaded.')

		self.local_proposal_labels = [None] * self.n_local_proposals


	def load_global_proposals(self):
		
		self.global_proposal_tuples =  self.dm.load_pipeline_result('proposals')
		self.global_proposal_clusters = [m[0] for m in self.global_proposal_tuples]
		self.global_proposal_dedges = [m[1] for m in self.global_proposal_tuples]
		self.global_proposal_sigs = [m[2] for m in self.global_proposal_tuples]

		self.n_global_proposals = len(self.global_proposal_tuples)

		if not hasattr(self, 'global_proposal_pathPatches'):
			self.global_proposal_pathPatches = [None] * self.n_global_proposals
			self.global_proposal_vertexCircles = [None] * self.n_global_proposals

		self.statusBar().showMessage('%d global proposals loaded' % self.n_global_proposals)

		self.sp_covered_by_proposals = self.dm.load_pipeline_result('spCoveredByProposals')
		self.sp_covered_by_proposals = dict([(s, list(props)) for s, props in self.sp_covered_by_proposals.iteritems()])

		self.global_proposal_labels = [None] * self.n_global_proposals


	def load_callback1(self):
		self.load_callback(panel=1)

	def load_callback2(self):
		self.load_callback(panel=2)

	def load_callback3(self):
		self.load_callback(panel=3)

	def load_labelings(self, accepted_proposal_props, gscene, sec):

		for props in accepted_proposal_props:
			
			curr_polygon_path = QPainterPath()

			for i, (x, y) in enumerate(props['vertices']):
				if i == 0:
					curr_polygon_path.moveTo(x,y)
				else:
					curr_polygon_path.lineTo(x,y)

			if props['subtype'] == PolygonType.CLOSED:
				curr_polygon_path.closeSubpath()

			polygon = QGraphicsPathItemModified(curr_polygon_path, gui=self)
			polygon.setPen(self.red_pen)
			polygon.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemClipsToShape | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemSendsScenePositionChanges)

			polygon.signal_emitter.clicked.connect(self.polygon_pressed)
			polygon.signal_emitter.moved.connect(self.polygon_moved)
			polygon.signal_emitter.released.connect(self.polygon_released)

			polygon.setZValue(50)

			polygon.setPath(curr_polygon_path)

			gscene.addItem(polygon)

			# elif props['subtype'] == PolygonType.OPEN:
			# else:
			# 	raise 'unknown polygon type'

			props['vertexCircles'] = []
			for x, y in props['vertices']:

				ellipse = QGraphicsEllipseItemModified(-VERTEX_CIRCLE_RADIUS, -VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS, 2*VERTEX_CIRCLE_RADIUS, gui=self)
				ellipse.setPos(x,y)
				
				ellipse.setPen(Qt.blue)
				ellipse.setBrush(Qt.blue)

				ellipse.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemClipsToShape | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemSendsScenePositionChanges)
				ellipse.signal_emitter.moved.connect(self.vertex_moved)
				ellipse.signal_emitter.clicked.connect(self.vertex_clicked)
				ellipse.signal_emitter.released.connect(self.vertex_released)

				gscene.addItem(ellipse)

				ellipse.setZValue(99)

				props['vertexCircles'].append(ellipse)

			textItem = QGraphicsSimpleTextItem(QString(props['label']))

			if 'labelPos' not in props:
				centroid = np.mean([(v.scenePos().x(), v.scenePos().y()) for v in props['vertexCircles']], axis=0)
				textItem.setPos(centroid[0], centroid[1])
			else:
				textItem.setPos(props['labelPos'][0], props['labelPos'][1])

			textItem.setScale(1.5)

			textItem.setFlags(QGraphicsItem.ItemIgnoresTransformations | QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemClipsToShape | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemSendsScenePositionChanges)

			textItem.setZValue(99)
			props['labelTextArtist'] = textItem

			gscene.addItem(textItem)

			props.pop('vertices')

			# self.accepted_proposals[polygon] = props
			# self.accepted_proposals_allSections[self.selected_section][polygon] = props
			self.accepted_proposals_allSections[sec][polygon] = props


	def load_callback(self, panel=None):

		if panel == 1:
			gscene = self.section1_gscene
			# gview = self.section1_gview
			self.selected_section = self.section
		elif panel == 2:
			gscene = self.section2_gscene
			# gview = self.section2_gview
			self.selected_section = self.section2
		elif panel == 3:
			gscene = self.section3_gscene
			# gview = self.section3_gview
			self.selected_section = self.section3
		else:
			return

		fname = str(QFileDialog.getOpenFileName(self, 'Open file', self.dms[self.selected_section].labelings_dir))
		stack, sec, username, timestamp, suffix = os.path.basename(fname[:-4]).split('_')
		_, _, _, accepted_proposal_props = self.dms[self.selected_section].load_proposal_review_result(username, timestamp, suffix)

		self.accepted_proposals_allSections[self.selected_section] = defaultdict(dict)

		self.load_labelings(accepted_proposal_props, gscene, self.selected_section)
			

	def open_label_selection_dialog(self):

		print 'open_label_selection_dialog'

		if hasattr(self, 'recent_labels') and self.recent_labels is not None and len(self.recent_labels) > 0:
			self.structure_names = OrderedDict([(abbr, fullname) for abbr, fullname in self.structure_names.iteritems() if abbr in self.recent_labels] + \
							[(abbr, fullname) for abbr, fullname in self.structure_names.iteritems() if abbr not in self.recent_labels])

		self.label_selection_dialog = AutoCompleteInputDialog(parent=self, labels=[abbr + ' (' + fullname + ')' for abbr, fullname in self.structure_names.iteritems()])
		# self.label_selection_dialog = QInputDialog(self)
		self.label_selection_dialog.setWindowTitle('Select landmark label')

		# if hasattr(self, 'invalid_labelname'):
		#     print 'invalid_labelname', self.invalid_labelname
		# else:
		#     print 'no labelname set'

		if 'label' in self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]:
			self.label_selection_dialog.comboBox.setEditText(self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['label']+' ('+self.structure_names[self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['label']]+')')
		else:
			self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['label'] = ''

		self.label_selection_dialog.set_test_callback(self.label_dialog_text_changed)

		# self.label_selection_dialog.accepted.connect(self.label_dialog_text_changed)
		# self.label_selection_dialog.textValueSelected.connect(self.label_dialog_text_changed)

		self.label_selection_dialog.exec_()

	def label_dialog_text_changed(self):

		print 'label_dialog_text_changed'

		text = str(self.label_selection_dialog.comboBox.currentText())

		import re
		m = re.match('^(.+?)\s*\((.+)\)$', text)

		if m is None:
			QMessageBox.warning(self, 'oops', 'structure name must be of the form "abbreviation (full description)"')
			return

		else:
			abbr, fullname = m.groups()
			if not (abbr in self.structure_names.keys() and fullname in self.structure_names.values()):  # new label
				if abbr in self.structure_names:
					QMessageBox.warning(self, 'oops', 'structure with abbreviation %s already exists: %s' % (abbr, fullname))
					return
				else:
					self.structure_names[abbr] = fullname
					self.new_labelnames[abbr] = fullname

		print self.accepted_proposals_allSections.keys()
		print self.selected_section

		self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['label'] = abbr

		if 'labelTextArtist' in self.accepted_proposals_allSections[self.selected_section][self.selected_polygon] and self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['labelTextArtist'] is not None:
			self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['labelTextArtist'].setText(abbr)
		else:
			textItem = QGraphicsSimpleTextItem(QString(abbr))
			self.gscenes[self.selected_section].addItem(textItem)

			print self.selected_polygon
			centroid = np.mean([(v.scenePos().x(), v.scenePos().y()) for v in self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['vertexCircles']], axis=0)
			print centroid
			textItem.setPos(centroid[0], centroid[1])
			textItem.setScale(1.5)

			textItem.setFlags(QGraphicsItem.ItemIgnoresTransformations | QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemClipsToShape | QGraphicsItem.ItemSendsGeometryChanges | QGraphicsItem.ItemSendsScenePositionChanges)

			self.accepted_proposals_allSections[self.selected_section][self.selected_polygon]['labelTextArtist'] = textItem

			textItem.setZValue(99)

			# vertices = self.vertices_from_vertexPatches(self.selected_proposal_polygon)
			# centroid = np.mean(vertices, axis=0)
			# text_artist = Text(centroid[0], centroid[1], abbr, style='italic', bbox={'facecolor':'white', 'alpha':0.5, 'pad':10})
			# self.accepted_proposals[self.selected_proposal_polygon]['labelTextArtist'] = text_artist
			# self.axis.add_artist(text_artist)
			# text_artist.set_picker(True)

		self.recent_labels.insert(0, abbr)
		# self.invalid_labelname = None

		self.label_selection_dialog.accept()

	# def shuffle_proposal_from_pool(self, sp_ind):

	# 	if self.shuffle_global_proposals:   
	# 		if not hasattr(self, 'sp_covered_by_proposals'):
	# 			return
	# 	else:
	# 		if not hasattr(self, 'local_proposal_indices_from_sp'):
	# 			return

	# 	if self.shuffle_global_proposals:

	# 		if sp_ind not in self.sp_covered_by_proposals or sp_ind == -1:
	# 			self.statusBar().showMessage('No proposal covers superpixel %d' % sp_ind)
	# 			return 
	# 	else:
	# 		if sp_ind == -1:
	# 			return
		
	# 	if self.object_picked:
	# 		return

	# 	self.cancel_current_selection()

	# 	if self.shuffle_global_proposals:
	# 		self.selected_proposal_type = ProposalType.GLOBAL

	# 		self.alternative_global_proposal_ind = (self.alternative_global_proposal_ind + 1) % len(self.sp_covered_by_proposals[sp_ind])
	# 		self.selected_proposal_id = self.sp_covered_by_proposals[sp_ind][self.alternative_global_proposal_ind]

	# 		dedges = self.global_proposal_dedges[self.selected_proposal_id]
	# 	else:

	# 		self.selected_proposal_type = ProposalType.LOCAL

	# 		self.alternative_local_proposal_ind = (self.alternative_local_proposal_ind + 1) % len(self.local_proposal_indices_from_sp[sp_ind])
	# 		self.selected_proposal_id = self.local_proposal_indices_from_sp[sp_ind][self.alternative_local_proposal_ind]

	# 		cl, dedges, sig = self.local_proposal_tuples[self.selected_proposal_id]


	# 	if self.shuffle_global_proposals:
	# 		proposal_pathPatches = self.global_proposal_pathPatches
	# 		proposal_vertexCircles = self.global_proposal_vertexCircles
	# 	else:
	# 		proposal_pathPatches = self.local_proposal_pathPatches
	# 		proposal_vertexCircles = self.local_proposal_vertexCircles

	# 	if proposal_pathPatches[self.selected_proposal_id] is None:  
	# 		vertices = self.dm.vertices_from_dedges(dedges)

	# 		proposal_pathPatches[self.selected_proposal_id] = Polygon(vertices, closed=True, 
	# 								edgecolor=self.boundary_colors[0], fill=False, linewidth=UNSELECTED_POLYGON_LINEWIDTH)
	# 		proposal_vertexCircles[self.selected_proposal_id] = [plt.Circle(v, radius=UNSELECTED_CIRCLE_SIZE, color=self.boundary_colors[0], alpha=.8) for v in vertices]

	# 	if self.shuffle_global_proposals:
	# 		self.selected_proposal_polygon = self.global_proposal_pathPatches[self.selected_proposal_id]
	# 		self.selected_proposal_vertexCircles = self.global_proposal_vertexCircles[self.selected_proposal_id]
	# 	else:
	# 		self.selected_proposal_polygon = self.local_proposal_pathPatches[self.selected_proposal_id]
	# 		self.selected_proposal_vertexCircles = self.local_proposal_vertexCircles[self.selected_proposal_id]            

	# 	if self.selected_proposal_polygon not in self.axis.patches:
	# 		self.axis.add_patch(self.selected_proposal_polygon)

	# 		for vertex_circ in self.selected_proposal_vertexCircles:
	# 			self.axis.add_patch(vertex_circ)

	# 	self.selected_proposal_polygon.set_picker(None)
	# 	for vertex_circ in self.selected_proposal_vertexCircles:
	# 		vertex_circ.set_picker(None)

	# 	if self.selected_proposal_polygon in self.accepted_proposals_allSections[self.selected_section]:
	# 		self.selected_proposal_polygon.set_linewidth(UNSELECTED_POLYGON_LINEWIDTH)
	# 		label =  self.accepted_proposals_allSections[self.selected_section][self.selected_proposal_polygon]['label']
	# 	else:
	# 		label = ''

	# 	if self.shuffle_global_proposals:
	# 		self.statusBar().showMessage('global proposal (%s) covering seed %d, score %.4f' % (label, sp_ind, self.global_proposal_sigs[self.selected_proposal_id]))
	# 	else:
	# 		self.statusBar().showMessage('local proposal (%s) from seed %d, score %.4f' % (label, sp_ind, sig))

	# 	self.canvas.draw()


	def init_section_selected(self, item):
		self.statusBar().showMessage('Loading...')
		sec = int(str(item.text()))

		self.section = sec
		self.section2 = sec + 1
		self.section3 = sec - 1

		# self.load_active_set(sections=range(sec-1, sec+2))
		self.load_active_set(sections=range(sec-NUM_NEIGHBORS_PRELOAD, sec+NUM_NEIGHBORS_PRELOAD+1))
		
		self.extend_head = False
		self.connecting_vertices = False

		self.seg_loaded = False
		self.superpixels_on = False
		self.labels_on = True
		self.contours_on = True
		self.vertices_on = True

		# self.shuffle_global_proposals = True # instead of local proposals

		self.pressed = False           # related to pan (press and drag) vs. select (click)

		for gview in [self.section1_gview, self.section2_gview, self.section3_gview]:
			gview.setMouseTracking(False)
			gview.setVerticalScrollBarPolicy( Qt.ScrollBarAlwaysOff ) 
			gview.setHorizontalScrollBarPolicy( Qt.ScrollBarAlwaysOff ) 
			gview.setAlignment(Qt.AlignLeft | Qt.AlignTop)
			gview.setTransformationAnchor(QGraphicsView.NoAnchor)
			gview.setContextMenuPolicy(Qt.CustomContextMenu)

			gview.customContextMenuRequested.connect(self.showContextMenu)

			# if not hasattr(self, 'contextMenu_set') or (hasattr(self, 'contextMenu_set') and not self.contextMenu_set):
			# 	gview.customContextMenuRequested.connect(self.showContextMenu)
			# 	self.contextMenu_set = True

			gview.viewport().installEventFilter(self)

		# if not hasattr(self, 'username') or self.username is None:
		# 	username, okay = QInputDialog.getText(self, "Username", "Please enter username of the labelings you want to load:", QLineEdit.Normal, 'yuncong')
		# 	if not okay: return
		# 	self.username_toLoad = str(username)

		self.paint_panel(0, self.section)
		self.paint_panel(1, self.section2)
		self.paint_panel(2, self.section3)

		self.show()

		self.setWindowTitle('BrainLabelingGUI, stack %s'%self.stack + ', Left %d' %self.section3 + ' (%.3f)' % self.lateral_position_lookup[self.section3] + \
											', middle %d'%self.section + ' (%.3f)' % self.lateral_position_lookup[self.section] + \
											', right %d'%self.section2 + ' (%.3f)' % self.lateral_position_lookup[self.section2])

		self.set_mode(Mode.IDLE)

		self.statusBar().showMessage('Loaded')


	def clicked_navMap(self, event):

		scene_x = event.scenePos().x()
		scene_y = event.scenePos().y()

		cur_data_left, cur_data_right = self.axis.get_xlim()
		cur_data_bottom, cur_data_top = self.axis.get_ylim()

		cur_data_center_x = .5 * cur_data_left + .5 * cur_data_right
		cur_data_center_y = .5 * cur_data_bottom + .5 * cur_data_top

		offset_x = scene_x / self.navMap_scaling_x - cur_data_center_x
		offset_y = scene_y / self.navMap_scaling_y - cur_data_center_y

		# print offset_x, offset_y

		new_data_left = cur_data_left + offset_x
		new_data_right = cur_data_right + offset_x
		new_data_bottom = cur_data_bottom + offset_y
		new_data_top = cur_data_top + offset_y

		# print new_data_left, new_data_right, new_data_bottom, new_data_top

		if new_data_right > self.dm.image_width:
			return
		if new_data_bottom > self.dm.image_height:
			return
		if new_data_left < 0:
			return
		if new_data_top < 0:
			return

		self.navRect.setRect(new_data_left * self.navMap_scaling_x, new_data_top * self.navMap_scaling_y, 
			self.navMap_scaling_x * (new_data_right - new_data_left), 
			self.navMap_scaling_y * (new_data_bottom - new_data_top))

		self.graphicsScene_navMap.update(0, 0, self.graphicsView_navMap.size().width(), self.graphicsView_navMap.size().height())
		self.graphicsView_navMap.setSceneRect(0, 0, self.dm.image_width*self.navMap_scaling_x, self.dm.image_height*self.navMap_scaling_y)

		self.axis.set_xlim([new_data_left, new_data_right])
		self.axis.set_ylim([new_data_bottom, new_data_top])

		self.canvas.draw()


	def save_selected_section(self, sec=None):

		timestamp = datetime.datetime.now().strftime("%m%d%Y%H%M%S")

		if not hasattr(self, 'username') or self.username is None:
			username, okay = QInputDialog.getText(self, "Username", "Please enter your username:", QLineEdit.Normal, 'anon')
			if not okay: return
			self.username = str(username)
			self.lineEdit_username.setText(self.username)

		if sec is not None:

			# print self.accepted_proposals_allSections[sec]

			accepted_proposal_props = []
			for polygon, props in self.accepted_proposals_allSections[sec].iteritems():

				props_saved = props.copy()

				# props_saved['vertices'] = [(v.scenePos().x(), v.scenePos().y()) for v in props['vertexCircles']]

				path = polygon.path()

				if path.elementCount() > 1 and self.is_path_closed(path):
					props_saved['subtype'] = PolygonType.CLOSED
					props_saved['vertices'] = [(path.elementAt(i).x, path.elementAt(i).y) for i in range(path.elementCount()-1)]
				else:
					props_saved['subtype'] = PolygonType.OPEN
					props_saved['vertices'] = [(path.elementAt(i).x, path.elementAt(i).y) for i in range(path.elementCount())]

				label_pos = props['labelTextArtist'].scenePos()
				props_saved['labelPos'] = (label_pos.x(), label_pos.y())

				props_saved.pop('vertexCircles')
				props_saved.pop('labelTextArtist')

				accepted_proposal_props.append(props_saved)

			# print '#############'
			# print accepted_proposal_props

			labeling_path = self.dms[sec].save_proposal_review_result(accepted_proposal_props, self.username, timestamp, suffix='consolidated')

			# print self.new_labelnames
			self.dms[sec].add_labelnames(self.new_labelnames, os.environ['REPO_DIR']+'/visualization/newStructureNames.txt')

			self.statusBar().showMessage('Labelings saved to %s' % (self.username+'_'+timestamp))

			if sec in self.gscenes:
				pix = QPixmap(self.dms[sec].image_width/8, self.dms[sec].image_height/8)
				painter = QPainter(pix)
				
				self.gscenes[sec].render(painter, QRectF(0,0,self.dms[sec].image_width/8, self.dms[sec].image_height/8), 
										QRectF(0,0,self.dms[sec].image_width, self.dms[sec].image_height))
				pix.save(labeling_path[:-4] + '.jpg', "JPG")
				print 'Preview image saved to', labeling_path[:-4] + '.jpg'
				del painter
				del pix


	def save_callback(self):

		for sec, ac in self.accepted_proposals_allSections.iteritems():
			if sec in self.gscenes and sec in self.dms:
				print sec
				self.save_selected_section(sec)


		# timestamp = datetime.datetime.now().strftime("%m%d%Y%H%M%S")

		# if not hasattr(self, 'username') or self.username is None:
		# 	username, okay = QInputDialog.getText(self, "Username", "Please enter your username:", QLineEdit.Normal, 'anon')
		# 	if not okay: return
		# 	self.username = str(username)
		# 	self.lineEdit_username.setText(self.username)


		# for sec, ac in self.accepted_proposals_allSections.iteritems():

		# 	if len(ac.keys()) == 0:
		# 		continue

		# 	accepted_proposal_props = []
		# 	for polygon, props in ac.iteritems():

		# 		props_saved = props.copy()

		# 		# props_saved['vertices'] = [(v.scenePos().x(), v.scenePos().y()) for v in props['vertexCircles']]

		# 		path = polygon.path()

		# 		if self.is_path_closed(polygon.path()):
		# 			props_saved['subtype'] = PolygonType.CLOSED
		# 			props_saved['vertices'] = [(path.elementAt(i).x, path.elementAt(i).y) for i in range(path.elementCount()-1)]
		# 		else:
		# 			props_saved['subtype'] = PolygonType.OPEN
		# 			props_saved['vertices'] = [(path.elementAt(i).x, path.elementAt(i).y) for i in range(path.elementCount())]

		# 		label_pos = props['labelTextArtist'].scenePos()
		# 		props_saved['labelPos'] = (label_pos.x(), label_pos.y())

		# 		props_saved.pop('vertexCircles')
		# 		props_saved.pop('labelTextArtist')

		# 		accepted_proposal_props.append(props_saved)

		# 	labeling_path = self.dms[sec].save_proposal_review_result(accepted_proposal_props, self.username, timestamp, suffix='consolidated')

		# 	# print self.new_labelnames
		# 	self.dms[sec].add_labelnames(self.new_labelnames, os.environ['LOCAL_REPO_DIR']+'/visualization/newStructureNames.txt')

		# 	self.statusBar().showMessage('Labelings saved to %s' % (self.username+'_'+timestamp))

		# 	if sec in self.gscenes:
		# 		pix = QPixmap(self.dms[sec].image_width/8, self.dms[sec].image_height/8)
		# 		painter = QPainter(pix)
				
		# 		self.gscenes[sec].render(painter, QRectF(0,0,self.dms[sec].image_width/8, self.dms[sec].image_height/8), 
		# 								QRectF(0,0,self.dms[sec].image_width, self.dms[sec].image_height))
		# 		pix.save(labeling_path[:-4] + '.jpg', "JPG")
		# 		del painter


	def labelbutton_callback(self):
		pass

	############################################
	# matplotlib canvas CALLBACKs
	############################################


	def undo(self):

		if len(self.history_allSections[self.selected_section]) == 0:
			return

		history_item = self.history_allSections[self.selected_section].pop()

		if history_item['type'] == 'drag_polygon':

			polygon = history_item['polygon']
			moved_x, moved_y = history_item['mouse_moved']

			for circ in self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles']:
				curr_pos = circ.scenePos()
				circ.setPos(curr_pos.x() - moved_x, curr_pos.y() - moved_y)

			path = polygon.path()
			for i in range(polygon.path().elementCount()):
				elem = polygon.path().elementAt(i)
				scene_pos = polygon.mapToScene(elem.x, elem.y)
				path.setElementPositionAt(i, scene_pos.x() - moved_x, scene_pos.y() - moved_y)

			polygon.setPath(path)

			if 'labelTextArtist' in self.accepted_proposals_allSections[self.selected_section][polygon]:
				curr_label_pos = self.accepted_proposals_allSections[self.selected_section][polygon]['labelTextArtist'].scenePos()
				self.accepted_proposals_allSections[self.selected_section][polygon]['labelTextArtist'].setPos(curr_label_pos.x() - moved_x, curr_label_pos.y() - moved_y)

			# self.section1_gscene.update(0, 0, self.section1_gview.width(), self.section1_gview.height())
			self.gscenes[self.selected_section].update(0, 0, self.gviews[self.selected_panel_id].width(), self.gviews[self.selected_panel_id].height())

		elif history_item['type'] == 'drag_vertex':

			polygon = history_item['polygon']
			vertex = history_item['vertex']
			moved_x, moved_y = history_item['mouse_moved']

			curr_pos = vertex.scenePos()
			vertex.setPos(curr_pos.x() - moved_x, curr_pos.y() - moved_y)

			vertex_index = self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles'].index(vertex)

			path = polygon.path()
			elem_first = path.elementAt(0)
			elem_last = path.elementAt(path.elementCount()-1)
			is_closed = (elem_first.x == elem_last.x) & (elem_first.y == elem_last.y)

			if vertex_index == 0 and is_closed:
				path.setElementPositionAt(0, curr_pos.x() - moved_x, curr_pos.y() - moved_y)
				path.setElementPositionAt(len(self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles']), curr_pos.x() - moved_x, curr_pos.y() - moved_y)
			else:
				path.setElementPositionAt(vertex_index, curr_pos.x() - moved_x, curr_pos.y() - moved_y)

			polygon.setPath(path)

			# self.section1_gscene.update(0, 0, self.section1_gview.width(), self.section1_gview.height())
			self.gscenes[self.selected_section].update(0, 0, self.gviews[self.selected_panel_id].width(), self.gviews[self.selected_panel_id].height())


		elif history_item['type'] == 'add_vertex':
			polygon = history_item['polygon']
			index = history_item['index']

			vertex = self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles'][index]
			# vertex = history_item['vertex']

			path = polygon.path()
			elem_first = path.elementAt(0)
			elem_last = path.elementAt(path.elementCount()-1)
			is_closed = (elem_first.x == elem_last.x) & (elem_first.y == elem_last.y)
			print 'is_closed', is_closed

			path = QPainterPath()

			n = len(self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles'])

			if n == 1:
				# if only one vertex in polygon, then undo removes the entire polygon
				# self.section1_gscene.removeItem(polygon)
				self.gscenes[self.selected_section].removeItem(polygon)
				if 'labelTextArtist' in self.accepted_proposals_allSections[self.selected_section][polygon]:
					# self.section1_gscene.removeItem(self.accepted_proposals_allSections[self.selected_section][polygon]['labelTextArtist'])
					self.gscenes[self.selected_section].removeItem(self.accepted_proposals_allSections[self.selected_section][polygon]['labelTextArtist'])
				self.accepted_proposals_allSections[self.selected_section].pop(polygon)
				# self.section1_gscene.removeItem(vertex)
				self.gscenes[self.selected_section].removeItem(vertex)

				self.set_mode(Mode.IDLE)
			else:

				if not is_closed:
					# if it is open, then undo removes the last vertex
					self.accepted_proposals_allSections[self.selected_section][polygon]['vertexCircles'].remove(vertex)
					# self.section1_gscene.removeItem(vertex)
					self.gscenes[self.selected_section].removeItem(vertex)

					for i in range(n-1):
						elem = polygon.path().elementAt(i)
						if i == 0:
							path.moveTo(elem.x, elem.y)
						else:
							path.lineTo(elem.x, elem.y)
				else:
					# if it is closed, then undo opens it, without removing any vertex
					for i in range(n):
						elem = polygon.path().elementAt(i)
						if i == 0:
							path.moveTo(elem.x, elem.y)
						else:
							path.lineTo(elem.x, elem.y)

				polygon.setPath(path)

		elif history_item['type'] == 'set_uncertain_segment':

			old_polygon = history_item['old_polygon']
			new_certain_polygon = history_item['new_certain_polygon']
			new_uncertain_polygon = history_item['new_uncertain_polygon']

			label = self.accepted_proposals_allSections[self.selected_section][new_certain_polygon]['label']

			self.remove_polygon(new_certain_polygon)
			self.remove_polygon(new_uncertain_polygon)

			# self.section1_gscene.addItem(old_polygon)
			self.gscenes[self.selected_section].removeItem(vertex)
			overlap_polygons = self.add_vertices_to_polygon(old_polygon)
			self.restack_polygons(old_polygon, overlap_polygons)
			self.add_label_to_polygon(old_polygon, label=label)


	def set_mode(self, mode):

		if hasattr(self, 'mode'):
			if self.mode != mode:
				print self.mode, '=>', mode
		else:
			print mode

		self.mode = mode

		if mode == Mode.MOVING_VERTEX:
			self.set_flag_all(QGraphicsItem.ItemIsMovable, True)
		else:
			# if hasattr(self, 'accepted_proposals'):
			# 	for p, props in self.accepted_proposals.iteritems():
			# 		if p is None or 'vertexCircles' not in props or 'label' not in props:
			# 			self.remove_polygon(p)

			self.set_flag_all(QGraphicsItem.ItemIsMovable, False)

		if mode == Mode.SELECT_UNCERTAIN_SEGMENT or mode == Mode.DELETE_ROI_MERGE or mode == Mode.DELETE_ROI_DUPLICATE:
			self.gviews[self.selected_panel_id].setDragMode(QGraphicsView.RubberBandDrag)
		else:
			self.gviews[self.selected_panel_id].setDragMode(QGraphicsView.NoDrag)

		self.statusBar().showMessage(self.mode.value)

		
	def update_navMap(self):

		cur_xmin, cur_xmax = self.axis.get_xlim()
		cur_ybottom, cur_ytop = self.axis.get_ylim()
		self.navRect.setRect(cur_xmin * self.navMap_scaling_x, cur_ybottom * self.navMap_scaling_y, self.navMap_scaling_x * (cur_xmax - cur_xmin), self.navMap_scaling_y * (cur_ytop - cur_ybottom))
		self.graphicsScene_navMap.update(0, 0, self.graphicsView_navMap.size().width(), self.graphicsView_navMap.size().height())
		self.graphicsView_navMap.setSceneRect(0, 0, self.dm.image_width*self.navMap_scaling_x, self.dm.image_height*self.navMap_scaling_y)



	def find_proper_offset(self, offset_x, offset_y):

		if self.cur_ylim[0] - offset_y > self.dm.image_height:
			offset_y = self.cur_ylim[0] - self.dm.image_height
		elif self.cur_ylim[1] - offset_y < 0:
			offset_y = self.cur_ylim[1]

		if self.cur_xlim[1] - offset_x > self.dm.image_width:
			offset_x = self.dm.image_width - self.cur_xlim[1]
		elif self.cur_xlim[0] - offset_x < 0:
			offset_x = self.cur_xlim[0]

		return offset_x, offset_y



	def find_vertex_insert_position(self, polygon, x, y):

		path = polygon.path()
		is_closed = self.is_path_closed(path)

		pos = (x,y)

		xys = []
		for i in range(path.elementCount()):
			elem = path.elementAt(i)
			xys.append((elem.x, elem.y))

		n = len(xys)
		if n == 1:
			return 1

		xys_homo = np.column_stack([xys, np.ones(n,)])

		if is_closed:
			edges = np.array([np.cross(xys_homo[i], xys_homo[(i+1)%n]) for i in range(n)])
		else:
			edges = np.array([np.cross(xys_homo[i], xys_homo[i+1]) for i in range(n-1)])

		edges_normalized = edges/np.sqrt(np.sum(edges[:,:2]**2, axis=1))[:, np.newaxis]

		signed_dists = np.dot(edges_normalized, np.r_[pos,1])
		dists = np.abs(signed_dists)
		# sides = np.sign(signed_dists)

		projections = pos - signed_dists[:, np.newaxis] * edges_normalized[:,:2]

		endpoint = [None for _ in projections]
		for i, (px, py) in enumerate(projections):
			if (px > xys[i][0] and px > xys[(i+1)%n][0]) or (px < xys[i][0] and px < xys[(i+1)%n][0]):
				endpoint[i] = [i, (i+1)%n][np.argmin(np.squeeze(cdist([pos], [xys[i], xys[(i+1)%n]])))]
				dists[i] = np.min(np.squeeze(cdist([pos], [xys[i], xys[(i+1)%n]])))

		# print edges_normalized[:,:2]
		# print projections                
		# print dists
		# print endpoint
		nearest_edge_begins_at = np.argsort(dists)[0]

		if nearest_edge_begins_at == 0 and not is_closed and endpoint[0] == 0:
			new_vertex_ind = 0
		elif nearest_edge_begins_at == n-2 and not is_closed and endpoint[-1] == n-1:
			new_vertex_ind = n
		else:
			new_vertex_ind = nearest_edge_begins_at + 1  

		print 'nearest_edge_begins_at', nearest_edge_begins_at, 'new_vertex_ind', new_vertex_ind

		return new_vertex_ind

	# def find_vertex_insert_position(self, xys, pos, closed=True):

	# 	n = len(xys)
	# 	if n == 1:
	# 		return 1

	# 	xys_homo = np.column_stack([xys, np.ones(n,)])

	# 	if closed:
	# 		edges = np.array([np.cross(xys_homo[i], xys_homo[(i+1)%n]) for i in range(n)])
	# 	else:
	# 		edges = np.array([np.cross(xys_homo[i], xys_homo[i+1]) for i in range(n-1)])

	# 	edges_normalized = edges/np.sqrt(np.sum(edges[:,:2]**2, axis=1))[:, np.newaxis]

	# 	signed_dists = np.dot(edges_normalized, np.r_[pos,1])
	# 	dists = np.abs(signed_dists)
	# 	# sides = np.sign(signed_dists)

	# 	projections = pos - signed_dists[:, np.newaxis] * edges_normalized[:,:2]

	# 	endpoint = [None for _ in projections]
	# 	for i, (px, py) in enumerate(projections):
	# 		if (px > xys[i][0] and px > xys[(i+1)%n][0]) or (px < xys[i][0] and px < xys[(i+1)%n][0]):
	# 			endpoint[i] = [i, (i+1)%n][np.argmin(np.squeeze(cdist([pos], [xys[i], xys[(i+1)%n]])))]
	# 			dists[i] = np.min(np.squeeze(cdist([pos], [xys[i], xys[(i+1)%n]])))

	# 	# print edges_normalized[:,:2]
	# 	# print projections                
	# 	# print dists
	# 	# print endpoint
	# 	nearest_edge_begins_at = np.argsort(dists)[0]

	# 	if nearest_edge_begins_at == 0 and not closed and endpoint[0] == 0:
	# 		new_vertex_ind = 0
	# 	elif nearest_edge_begins_at == n-2 and not closed and endpoint[-1] == n-1:
	# 		new_vertex_ind = n
	# 	else:
	# 		new_vertex_ind = nearest_edge_begins_at + 1  

	# 	print 'nearest_edge_begins_at', nearest_edge_begins_at, 'new_vertex_ind', new_vertex_ind

	# 	return new_vertex_ind



	# def connect_two_vertices(self, polygon1, polygon2=None, index1=None, index2=None):

	# 	vertices1 = self.vertices_from_vertexPatches(polygon1)
	# 	n1 = len(vertices1)

	# 	if polygon2 is None: # connect two ends of a single polygon   
			
	# 		print 'index1', index1, 'index2', index2
	# 		assert not polygon1.get_closed()
	# 		 # and ((index1 == 0 and index2 == n2-1) or (index1 == n1-1 and index2 == 0))
	# 		polygon1.set_closed(True)
	# 		if 'label' not in self.accepted_proposals[polygon1]:
	# 			self.acceptProposal_callback()
		
	# 	else:

	# 		vertices2 = self.vertices_from_vertexPatches(polygon2)
	# 		n2 = len(vertices2)
	# 		print 'index1', index1, 'index2', index2
	# 		assert not polygon1.get_closed() and index1 in [0, n1-1] and index2 in [0, n2-1]

	# 		if index1 == 0 and index2 == 0:
	# 			new_vertices = np.vstack([vertices1[::-1], vertices2])
	# 		elif index1 != 0 and index2 != 0:
	# 			new_vertices = np.vstack([vertices1, vertices2[::-1]])
	# 		elif index1 != 0 and index2 == 0:
	# 			new_vertices = np.vstack([vertices1, vertices2])
	# 		elif index1 == 0 and index2 != 0:
	# 			new_vertices = np.vstack([vertices1[::-1], vertices2[::-1]])

	# 		props = {}

	# 		patch = Polygon(new_vertices, closed=False, edgecolor=self.boundary_colors[1], fill=False, linewidth=UNSELECTED_POLYGON_LINEWIDTH)
	# 		patch.set_picker(True)

	# 		self.axis.add_patch(patch)

	# 		props['vertexPatches'] = []
	# 		for x,y in new_vertices:
	# 			vertex_circle = plt.Circle((x, y), radius=UNSELECTED_CIRCLE_SIZE, color=self.boundary_colors[1], alpha=.8)
	# 			vertex_circle.set_picker(CIRCLE_PICK_THRESH)
	# 			props['vertexPatches'].append(vertex_circle)
	# 			self.axis.add_patch(vertex_circle)
	# 			vertex_circle.set_picker(True)

	# 		if self.accepted_proposals[polygon1]['label'] == self.accepted_proposals[polygon2]['label']:
	# 			props['label'] = self.accepted_proposals[polygon1]['label']
	# 		else:
	# 			props['label'] = self.accepted_proposals[polygon1]['label']
	# 		# else:
	# 			# self.acceptProposal_callback()

	# 		props['type'] = self.accepted_proposals[polygon1]['type']

	# 		centroid = np.mean(new_vertices, axis=0)
	# 		props['labelTextArtist'] = Text(centroid[0], centroid[1], props['label'], style='italic', bbox={'facecolor':'white', 'alpha':0.5, 'pad':10})

	# 		self.axis.add_artist(props['labelTextArtist'])
	# 		props['labelTextArtist'].set_picker(True)

	# 		self.accepted_proposals[patch] = props
			
	# 		for circ in self.accepted_proposals[polygon1]['vertexPatches']:
	# 			circ.remove()
			
	# 		for circ in self.accepted_proposals[polygon2]['vertexPatches']:
	# 			circ.remove()

	# 		self.accepted_proposals[polygon1]['labelTextArtist'].remove()
	# 		self.accepted_proposals[polygon2]['labelTextArtist'].remove()
			
	# 		polygon1.remove()
	# 		polygon2.remove()

	# 		self.accepted_proposals.pop(polygon1)
	# 		self.accepted_proposals.pop(polygon2)

	# 	self.cancel_current_selection()

	# 	self.canvas.draw()

	def split_path(self, path, vertex_indices):

		is_closed = self.is_path_closed(path)
		n = path.elementCount() - 1 if is_closed else path.elementCount()
		segs_in, segs_out = self.split_array(vertex_indices, n, is_closed)

		print segs_in, segs_out

		in_paths = []
		out_paths = []

		for b, e in segs_in:
			in_path = self.subpath(path, b, e)
			in_paths.append(in_path)

		for b, e in segs_out:
			out_path = self.subpath(path, b-1, e+1)
			out_paths.append(out_path)

		return in_paths, out_paths

	def split_array(self, vertex_indices, n, is_closed):

		cache = [i in vertex_indices for i in range(n)]

		i = 0

		sec_outs = []
		sec_ins = []

		sec_in = [None,None]
		sec_out = [None,None]

		while i != (n+1 if is_closed else n):

			if cache[i%n] and not cache[(i+1)%n]:
				sec_in[1] = i%n
				sec_ins.append(sec_in)
				sec_in = [None,None]

				sec_out[0] = (i+1)%n
			elif not cache[i%n] and cache[(i+1)%n]:
				sec_out[1] = i%n
				sec_outs.append(sec_out)
				sec_out = [None,None]

				sec_in[0] = (i+1)%n
			
			i += 1

		if sec_in[0] is not None or sec_in[1] is not None:
			sec_ins.append(sec_in)

		if sec_out[0] is not None or sec_out[1] is not None:
			sec_outs.append(sec_out)

		tmp = [None, None]
		for sec in sec_ins:
			if sec[0] is None and sec[1] is not None:
				tmp[1] = sec[1]
			elif sec[0] is not None and sec[1] is None:
				tmp[0] = sec[0]
		if tmp[0] is not None and tmp[1] is not None:
			sec_ins = [s for s in sec_ins if s[0] is not None and s[1] is not None] + [tmp]
		else:
			sec_ins = [s for s in sec_ins if s[0] is not None and s[1] is not None]

		tmp = [None, None]
		for sec in sec_outs:
			if sec[0] is None and sec[1] is not None:
				tmp[1] = sec[1]
			elif sec[0] is not None and sec[1] is None:
				tmp[0] = sec[0]
		if tmp[0] is not None and tmp[1] is not None:
			sec_outs = [s for s in sec_outs if s[0] is not None and s[1] is not None] + [tmp]
		else:
			sec_outs = [s for s in sec_outs if s[0] is not None and s[1] is not None]

		if not is_closed:
			sec_ins2 = []
			for sec in sec_ins:
				if sec[0] > sec[1]:
					sec_ins2.append([sec[0], n-1])
					sec_ins2.append([0, sec[1]])
				else:
					sec_ins2.append(sec)

			sec_outs2 = []
			for sec in sec_outs:
				if sec[0] > sec[1]:
					sec_outs2.append([sec[0], n-1])
					sec_outs2.append([0, sec[1]])
				else:
					sec_outs2.append(sec)

			return sec_ins2, sec_outs2

		else:
			return sec_ins, sec_outs


	def is_path_closed(self, path):

		elem_first = path.elementAt(0)
		elem_last = path.elementAt(path.elementCount()-1)
		is_closed = (elem_first.x == elem_last.x) & (elem_first.y == elem_last.y)

		return is_closed

	def delete_vertices(self, polygon, indices_to_remove, merge=False):

		if merge:
			self.delete_vertices_merge(polygon, indices_to_remove)
		else:
			paths_to_remove, paths_to_keep = self.split_path(polygon.path(), indices_to_remove)

			for path in paths_to_keep:
				self.add_polygon_vertices_label(path, pen=self.red_pen, label=self.accepted_proposals_allSections[self.selected_section][polygon]['label'])

			self.remove_polygon(polygon)


	def delete_between(self, polygon, first_index, second_index):

		print first_index, second_index

		if second_index < first_index:	# ensure first_index is smaller than second_index
			temp = first_index
			first_index = second_index
			second_index = temp

		path = polygon.path()
		is_closed = self.is_path_closed(path) 
		n = path.elementCount() - 1 if is_closed else path.elementCount()

		if (second_index - first_index > first_index + n - second_index):
			indices_to_remove = range(second_index, n+1) + range(0, first_index+1)
		else:
			indices_to_remove = range(first_index, second_index+1)

		print indices_to_remove

		paths_to_remove, paths_to_keep = self.split_path(path, indices_to_remove)

		for new_path in paths_to_keep:

			self.add_polygon_vertices_label(new_path, pen=self.red_pen, label=self.accepted_proposals_allSections[self.selected_section][polygon]['label'])	

		self.remove_polygon(polygon)


	def delete_vertices_merge(self, polygon, indices_to_remove):

		path = polygon.path()
		is_closed = self.is_path_closed(path) 
		n = path.elementCount() - 1 if is_closed else path.elementCount()

		segs_to_remove, segs_to_keep = self.split_array(indices_to_remove, n, is_closed)
		print segs_to_remove, segs_to_keep

		new_path = QPainterPath()
		for b, e in sorted(segs_to_keep):
			if e < b: e = e + n
			for i in range(b, e + 1):
				elem = path.elementAt(i % n)
				if new_path.elementCount() == 0:
					new_path.moveTo(elem.x, elem.y)
				else:
					new_path.lineTo(elem.x, elem.y)

		if is_closed:
			new_path.closeSubpath()
				
		self.add_polygon_vertices_label(new_path, pen=self.red_pen, label=self.accepted_proposals_allSections[self.selected_section][polygon]['label'])
		
		self.remove_polygon(polygon)

			
	def add_polygon_vertices_label(self, path, pen, label):
		new_polygon = self.add_polygon(path, pen)
		overlap_polygons = self.add_vertices_to_polygon(new_polygon)
		print overlap_polygons
		self.restack_polygons(new_polygon, overlap_polygons)
		self.add_label_to_polygon(new_polygon, label=label)

		return new_polygon

	def auto_extend_view(self, x, y):
		# always make just placed vertex at the center of the view

		viewport_scene_rect = self.section1_gview.viewport().rect()	# NOT UPDATING!!! WEIRD!!!
		cur_xmin = viewport_scene_rect.x()
		cur_ymin = viewport_scene_rect.y()
		cur_xmax = cur_xmin + viewport_scene_rect.width()
		cur_ymax = cur_ymin + viewport_scene_rect.height()

		print cur_xmin, cur_ymin, cur_xmax, cur_ymax

		if abs(x - cur_xmin) < AUTO_EXTEND_VIEW_TOLERANCE or abs(x - cur_xmax) < AUTO_EXTEND_VIEW_TOLERANCE:
			cur_xcenter = cur_xmin * .6 + cur_xmax * .4 if abs(x - cur_xmin) < AUTO_EXTEND_VIEW_TOLERANCE else cur_xmin * .4 + cur_xmax * .6
			translation_x = cur_xcenter - x

			self.section1_gview.translate(-translation_x, 0)
			self.section2_gview.translate(-translation_x, 0)
			self.section3_gview.translate(-translation_x, 0)

		if abs(y - cur_ymin) < AUTO_EXTEND_VIEW_TOLERANCE or abs(y - cur_ymax) < AUTO_EXTEND_VIEW_TOLERANCE:
			cur_ycenter = cur_ymin * .6 + cur_ymax * .4 if abs(y - cur_ymin) < AUTO_EXTEND_VIEW_TOLERANCE else cur_ymin * .4 + cur_ymax * .6
			translation_y = cur_ycenter - y

			self.section1_gview.translate(0, -translation_y)
			self.section2_gview.translate(0, -translation_y)
			self.section3_gview.translate(0, -translation_y)


	# def place_vertex(self, x, y):
	#     self.selected_proposal_vertices.append([x, y])

	#     # curr_vertex_circle = plt.Circle((x, y), radius=10, color=self.colors[self.curr_label + 1], alpha=.8)
	#     curr_vertex_circle = plt.Circle((x, y), radius=UNSELECTED_CIRCLE_SIZE, color=self.boundary_colors[1], alpha=.8)
	#     self.axis.add_patch(curr_vertex_circle)
	#     self.selected_proposal_vertexCircles.append(curr_vertex_circle)

	#     curr_vertex_circle.set_picker(CIRCLE_PICK_THRESH)

	#     self.auto_extend_view(x, y)

	#     self.canvas.draw()
	#     self.canvas2.draw()

	#     self.history.append({'type': 'add_vertex', 'selected_proposal_vertexCircles': self.selected_proposal_vertexCircles,
	#         'selected_proposal_vertices': self.selected_proposal_vertices})

		# print self.history


	def load_segmentation(self):
		sys.stderr.write('loading segmentation...\n')
		self.statusBar().showMessage('loading segmentation...')

		self.dm.load_multiple_results(results=[
		  'segmentation', 
		  'edgeEndpoints', 'edgeMidpoints'])
		self.segmentation = self.dm.load_pipeline_result('segmentation')
		self.n_superpixels = self.dm.segmentation.max() + 1

		self.seg_loaded = True
		sys.stderr.write('segmentation loaded.\n')

		sys.stderr.write('loading sp props...\n')
		self.statusBar().showMessage('loading sp properties..')
		# self.sp_centroids = self.dm.load_pipeline_result('spCentroids')
		# self.sp_bboxes = self.dm.load_pipeline_result('spBbox')
		sys.stderr.write('sp properties loaded.\n')

		self.statusBar().showMessage('')

		# self.sp_rectlist = [None for _ in range(self.dm.n_superpixels)]


	def turn_superpixels_off(self):
		self.statusBar().showMessage('Supepixels OFF')

		self.buttonSpOnOff.setText('Turn Superpixels ON')

		self.segm_handle.remove()
		self.superpixels_on = False
		
		# self.axis.imshow(self.masked_img, cmap=plt.cm.Greys_r,aspect='equal')
		# self.orig_image_handle = self.axis.imshow(self.masked_img, aspect='equal')

	def turn_superpixels_on(self):
		self.statusBar().showMessage('Supepixels ON')

		self.buttonSpOnOff.setText('Turn Superpixels OFF')

		if self.segm_transparent is None:
			self.segm_transparent = self.dm.load_pipeline_result('segmentationTransparent')
			self.my_cmap = plt.cm.Reds
			self.my_cmap.set_under(color="white", alpha="0")

		if not self.seg_loaded:
			self.load_segmentation()

		self.superpixels_on = True
		
		if hasattr(self, 'segm_handle'):
			self.segm_handle.set_data(self.segm_transparent)
		else:
			self.segm_handle = self.axis.imshow(self.segm_transparent, aspect='equal', 
								cmap=self.my_cmap, alpha=1.)



	def cancel_current_circle(self):

		if self.selected_circle is not None:
			self.selected_circle.set_radius(UNSELECTED_CIRCLE_SIZE)
			self.selected_circle = None

			self.selected_vertex_index = None


	def cancel_current_selection(self):

		if self.selected_proposal_polygon is not None:

			if self.selected_proposal_polygon.get_linewidth() != UNSELECTED_POLYGON_LINEWIDTH:
				self.selected_proposal_polygon.set_linewidth(UNSELECTED_POLYGON_LINEWIDTH)

			if self.selected_proposal_polygon in self.axis.patches:
				if self.selected_proposal_polygon not in self.accepted_proposals_allSections[self.selected_section]:
					self.selected_proposal_polygon.remove()
					for vertex_circ in self.selected_proposal_vertexCircles:
						vertex_circ.remove()

		self.selected_proposal_polygon = None
		self.selected_proposal_vertexCircles = None

		if self.selected_circle is not None:
			self.selected_circle.set_radius(UNSELECTED_CIRCLE_SIZE)
			self.selected_circle = None

			self.selected_vertex_index = None


		self.canvas.draw()


	def mode_changed(self):

		self.cancel_current_selection()

		if self.radioButton_globalProposal.isChecked():

			self.shuffle_global_proposals = True

			if not self.superpixels_on:
				self.turn_superpixels_on()

			if not hasattr(self, 'global_proposal_tuples'):
				self.load_global_proposals()

		elif self.radioButton_localProposal.isChecked():

			if not self.superpixels_on:
				self.turn_superpixels_on()

			self.shuffle_global_proposals = False

			if not hasattr(self, 'local_proposal_tuples'):
				self.load_local_proposals()

		self.canvas.draw()

	# def display_option_changed(self):
	# 	if self.sender() == self.buttonSpOnOff:

	# 		if not self.superpixels_on:
	# 			self.turn_superpixels_on()
	# 		else:
	# 			self.turn_superpixels_off()
	# 	else:
	# 		print 'not implemented'
	# 		return

	# 		# if self.under_img is not None:
	# 		#   self.under_img.remove()

	# 		self.axis.clear()

	# 		if self.sender() == self.img_radioButton:

	# 			# self.axis.clear()
	# 			# self.axis.axis('off')

	# 			# self.under_img = self.axis.imshow(self.masked_img, aspect='equal', cmap=plt.cm.Greys_r)
	# 			self.axis.imshow(self.dm.image_rgb_jpg, aspect='equal', cmap=plt.cm.Greys_r)
	# 			# self.superpixels_on = False

	# 		elif self.sender() == self.textonmap_radioButton:

	# 			# self.axis.clear()
	# 			# self.axis.axis('off')

	# 			if self.textonmap_vis is None:
	# 				self.textonmap_vis = self.dm.load_pipeline_result('texMapViz')

	# 			# if self.under_img is not None:
	# 			#   self.under_img.remove()

	# 			# self.under_img = self.axis.imshow(self.textonmap_vis, cmap=plt.cm.Greys_r, aspect='equal')
	# 			self.axis.imshow(self.textonmap_vis, cmap=plt.cm.Greys_r, aspect='equal')
	# 			# self.superpixels_on = False

	# 		elif self.sender() == self.dirmap_radioButton:

	# 			# self.axis.clear()
	# 			# self.axis.axis('off')

	# 			if self.dirmap_vis is None:
	# 				self.dirmap_vis = self.dm.load_pipeline_result('dirMap', 'jpg')
	# 				self.dirmap_vis[~self.dm.mask] = 0


	# 			# self.under_img = self.axis.imshow(self.dirmap_vis, aspect='equal')
	# 			self.axis.imshow(self.dirmap_vis, aspect='equal')

	# 			# if not self.seg_loaded:
	# 			#   self.load_segmentation()

	# 			# self.superpixels_on = False

	# 		# elif self.sender() == self.labeling_radioButton:
	# 		#   pass

	# 	self.axis.axis('off')
	# 	# self.axis.set_xlim([self.newxmin, self.newxmax])
	# 	# self.axis.set_ylim([self.newymin, self.newymax])
	# 	# self.fig.subplots_adjust(left=0, bottom=0, right=1, top=1)
	# 	self.canvas.draw()

	# 	self.axis2.axis('off')
	# 	# self.axis2.set_xlim([self.newxmin, self.newxmax])
	# 	# self.axis2.set_ylim([self.newymin, self.newymax])
	# 	# self.fig2.subplots_adjust(left=0, bottom=0, right=1, top=1)
	# 	self.canvas2.draw()

			   
if __name__ == "__main__":
	from sys import argv, exit
	appl = QApplication(argv)

	import argparse
	import sys
	import time

	parser = argparse.ArgumentParser(
	    formatter_class=argparse.RawDescriptionHelpFormatter,
	    description='Compute texton map')

	parser.add_argument("stack_name", type=str, help="stack name")
	parser.add_argument("-n", "--num_neighbors", type=int, help="number of neighbor sections to preload, default %(default)d", default=1)
	args = parser.parse_args()

	stack = args.stack_name
	NUM_NEIGHBORS_PRELOAD = args.num_neighbors
	m = BrainLabelingGUI(stack=stack)

	m.showMaximized()
	m.raise_()
	exit(appl.exec_())
