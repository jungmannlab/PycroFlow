"""
Created on Tue Oct  4 17:07:56 2022
​
@author: Jonas Fischer
"""
import pycromanager as pycro
from pycromanager import Acquisition, multi_d_acquisition_events
from time import sleep
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# =============================================================================
# Construct java objects
# =============================================================================
bridge = pycro.Bridge()
core = bridge.get_core()
#core=Core()
mm = bridge.get_studio()
pm = mm.positions()

# =============================================================================
# General values that are required for the measurements
#
# TODO: Implement importer for xy position list and their names from micromanager after test run works, framerate(mmc.set_property("Camera", "Framerate", framerate))
# =============================================================================
save_dir = r"X:\users\jfischer\1.RNA-PAINT\z.microscopy_raw\221007_30plex_test"
save_dir = r'Z:\users\grabmayr\FlowAutomation\testdata'
base_name = r'30plex'

# =============================================================================
# FOV and binning parameter settings
# =============================================================================
# core.set_property("Andor sCMOS Camera", "Binning", '2x2') # could also take argument 2 instead of 2x2
# core.set_property("Andor sCMOS Camera", "PixelReadoutRate", '540 MHz - fastest readout' )
# core.set_property('Andor sCMOS Camera','Sensitivity/DynamicRange', "16-bit (low noise & high well capacity)" )
#
# core.set_property('Andor sCMOS Camera','AcquisitionWindow', "1024x1024" ) # This takes the middle part of the image
core.set_auto_shutter(True)
# =============================================================================
# Channel specific settings
# =============================================================================
Dichro_channelname='Filter turret'
Dichro_list=['2-G561']
channel_exp_time = [100] # First list entry corresponds to exposure time of first channel in channel_list
#channel_framerate = ['9.9987', '33.327'] # TODO: How do I use the correct (maximum) framerate values for each exposure time

Exp_channelname= '1.Measurement_presets'
Exp_list=['30ms', '100ms']

# channel_frames = [10000, 5000] # First list entry corresponds to number of acquired frames of first channel in Dichro_list
channel_frames = [10] # First list entry corresponds to number of acquired frames of first channel in Dichro_list

# =============================================================================
# XY Position list
# =============================================================================
xy_pos_list = [[27544, -6345],[32044, -6345],[36544, -6345],[41044, -6345],[45544, -6345],[45544, -10845],[41044, -10845],[36544, -10845],[32044, -10845],[27544, -10845],[23044, -10845],[18544, -10845],[14044, -10845],[9544, -10845],[5044, -10845],[5044, -15345],[9544, -15345],[14044, -15345],[18544, -15345],[23044, -15345],[27544, -15345],[32044, -15345],[36544, -15345],[41044, -15345],[45544, -15345]] # only for testing
xy_pos_name_list = ['D13', 'D14', 'D15', 'D16', 'D17', 'E17', 'E16', 'E15', 'E14', 'E13', 'E12', 'E11', 'E10', 'E9', 'E8', 'F08', 'F09', 'F10', 'F11', 'F12', 'F13', 'F14', 'F15', 'F16', 'F17'] # only for testing

#[5044, -6345],[9544, -6345],[14044, -6345],[18544, -6345],[23044, -6345],
#'D08', 'D09', 'D10', 'D11', 'D12',​​
# xy_pos_list=[]
# xy_pos_name_list=[]
# pos_list = pm.get_position_list()

# for idx in range(pos_list.get_number_of_positions()):
#     pos = pos_list.get_position(idx)
#     print(pos)
#     # pos.go_to_position(pos, core)
#     xy_pos_name_list.append(pos.get_label())
#     for ipos in range (pos.size()):
#       stage_pos = pos.get(ipos)
#       xy_pos_list.append([stage_pos.x,stage_pos.y])

# for i in range(len(xy_pos_list)):
#     for j in range(len(Dichro_list)):
#         print(xy_pos_name_list[i])
#         print(xy_pos_list[i])
#         print(channel_list[j])

# filepath = r"C:\Users\admin\Desktop\Jonas\221004\1.Full_test\PositionList.pos"
# bridge = Bridge()
# studio = bridge.get_studio()
# position_list_manager = studio.get_position_list_manager()
# postition_list = position_list_manager.get_position_list()
# postition_list.load(filepath)
# nb_positions = postition_list.get_number_of_positions()
# position_list = []
# for i in range(nb_positions):
#     position = postition_list.get_position(i)
#     x = position.get_x()
#     y = position.get_y()

#     pos = stage_pos(x,y)
#     position_list.append(pos)

# print(position_list)​

# =============================================================================
# Full function to be executed
# =============================================================================

sleep(2) # To give the hardware time to adjust the parameters

#Function that drives to each position in the xy position list and acquires a time series for each dichro channel.
def record_multiple_pos_channels(acq_dir, base_name, xy_pos_list, xy_pos_name_list, channel_groupname, Dichro_list, channel_exp_time, channel_frames):
    for i in range(len(xy_pos_list)):
        # change the xy position to the next one
        # core.set_xy_position(xy_pos_list[i][0],xy_pos_list[i][1])
        # core.wait_for_device("XYStage") # Waits until the camera reports that it is no longer moving
        for j in range(len(Dichro_list)):
            # Set the channel to the right setting for each measuremen
            #core.set_config(channel_groupname, channel_list[j])
            core.set_config(Dichro_channelname, Dichro_list[j])
            # core.set_config(Exp_channelname, Exp_list[j])
            core.wait_for_device("HamamatsuHam_DCAM")
            #sleep(2) # To give the hardware time to adjust the parameters
            with Acquisition(directory=acq_dir, name= base_name + '_' + xy_pos_name_list[i] +'_' + Dichro_list[j], show_display=True) as acq:
                events = multi_d_acquisition_events(num_time_points=channel_frames[j], time_interval_s=0)
                acq.acquire(events, keep_shutter_open = True)

record_multiple_pos_channels(save_dir, base_name, xy_pos_list, xy_pos_name_list, Dichro_channelname, Dichro_list, channel_exp_time, channel_frames)
# =============================================================================
# Code snippets for testing
# =============================================================================
