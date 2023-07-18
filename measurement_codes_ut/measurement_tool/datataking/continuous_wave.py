import numpy as np
from logging import getLogger

import numpy as np
import itertools
import copy
import os
from tqdm import tqdm
from .instrument_manager_cw import InstrumentManagerBase
from plottr.data.datadict_storage import DataDict, DDH5Writer, datadict_from_hdf5
import time

import matplotlib.pyplot as plt

from measurement_codes_ut.measurement_tool import Session

logger = getLogger(__name__)


class ContinuousWaveInstrumentManager(InstrumentManagerBase):
    """Insturment management class for timedomain measurement"""

    def __init__(self, session: Session, save_path) -> None:
        """Constructor of CW measurement

        Args:
            session (Session): session of measurement
            config_name (str): default config name of instruments
        """
        # print("Creating a new insturment management class for timedomain measurement...", end="")
        super().__init__(session, save_path)

    def take_data(self, 
                  dataset_name: str, 
                  dataset_subpath: str = "CW", 
                  exp_file: str = None):
        """take data

        Args:
            dataset_name (str): dataset name
            dataset_subpath (str, optional): data is saved to specified subpath of datavault. Defaults to "".
            exp_file (str, optional): File name in which experiment is executed. Defaults to None, which saves no backup file except for this .py.
            # verbose (bool, optional): If true, show tqdm progress bar. Defaults to True.

        Returns:
            Dataset: taken dataset
        """
        sweep = {"VNA_freq":None, "VNA_power":None, "LO_freq":None, "LO_power":None, "Current":None}
        

        for name, port in self.port.items():
            if port.type == 'VNA':
                if isinstance(port.frequency, np.ndarray) or isinstance(port.frequency, list):
                    sweep['VNA_freq'] = np.array(port.frequency)
                else:
                    port.update_frequency()

                if isinstance(port.power, np.ndarray) or isinstance(port.power, list):
                    sweep['VNA_power'] = np.array(port.power)
                else:
                    port.update_power()

            if port.type == 'LO':
                if isinstance(port.frequency, np.ndarray) or isinstance(port.frequency, list):
                    sweep['LO_freq'] = np.array(port.frequency)
                else:
                    port.update_frequency()

                if isinstance(port.power, np.ndarray) or isinstance(port.power, list):
                    sweep['LO_power'] = np.array(port.power)
                else:
                    port.update_power()

            if port.type == 'Current Source':
                if isinstance(port.current, np.ndarray) or isinstance(port.current, list):
                    sweep['Current'] = np.array(port.current)
                else:
                    port.update_current()

        sweep_flag = {key:True if isinstance(sweep[key], np.ndarray) else False for key in sweep}
        
        vna = self.vna
        vna.s_parameter("S21")
        lo = self.lo
        current_source = self.current_source
        drive_flag = False
        if isinstance(sweep["VNA_freq"], np.ndarray):
            vna.sweep_type("linear frequency")
            vna.start(sweep['VNA_freq'][0])  # Hz
            vna.stop(sweep['VNA_freq'][-1])  # Hz
            vna.points(len(sweep['VNA_freq']))

        elif isinstance(sweep["LO_freq"], np.ndarray):
            vna_freq = vna.frequency()
            vna.sweep_type("linear frequency")
            vna.start(vna_freq)
            vna.stop(vna_freq)
            vna.points(len(sweep["LO_freq"]))
            vna.sweep_mode("hold")
            vna.trigger_source("external")
            vna.trigger_scope("current")
            vna.trigger_mode("point")
            try:
                vna.ctrl_s_port_4_function("aux trig 1 output")
                vna.aux_trig_1_output_enabled(True)
            except:
                vna.aux1.output(True)
            
            lo.frequency_mode("list")
            lo.point_trigger_source("external")
            lo.sweep_points(len(sweep["LO_freq"]))
            drive_flag = True

        var_dict = {}
        for key in sweep:
            if isinstance(sweep[key], np.ndarray) and key in ["VNA_power", "LO_power", "Current"]:
                if "power" in key:
                    var_dict[key] = dict(unit="dBm")
                else:
                    var_dict[key] = dict(unit="A")
                    
        var_dict['S21'] = dict(axes=list(var_dict.keys()))

        data = DataDict(**var_dict)
        data.validate()

        save_path = self.save_path + dataset_subpath + "/"

        with DDH5Writer(data, data_path, name=dataset_name) as writer:
            self.prepare_experiment(writer, exp_file)

            for cur in (tqdm(sweep['Current']) if sweep_flag['Current'] else [current_source.current()]):
                current_source.ramp_current(cur, step=1e-8, delay=0)
                for lo_power in (tqdm(sweep['LO_power']) if sweep_flag['LO_Power'] else [lo.power()]):
                    lo.power(lo_power)
                    for vna_power in (tqdm(sweep['VNA_power']) if sweep_flag['VNA_Power'] else [vna.power()]):
                        vna.power(vna_power)

                        if drive_flag:
                            self.run_drive_sweep()
                        else:
                            vna.run_sweep()

                        write_dict = {}
                        for key in sweep:
                            if sweep_flag[key]:
                                if key == 'LO_power':
                                    write_dict[key] = lo_power
                                if key == 'VNA_power':
                                    write_dict[key] = vna_power
                                if key == 'Current':
                                    write_dict[key] = cur
                        write_dict["S21"] = vna.trace()
                        writer.add_data(**write_dict)
         

        files = os.listdir(save_path)
        date = files[-1] + '/'
        files = os.listdir(save_path+date)
        data_path = files[-1]

        data_path_all = save_path+date+data_path + '/'

        dataset = datadict_from_hdf5(data_path_all+"data")

        return dataset
    


    def run_drive_sweep(self):
        vna = self.vna
        vna.output(True)
        drive_source = self.lo
        drive_source.output(True)
        drive_source.start_sweep()
        vna.sweep_mode("single")
        try:
            while not (vna.done() and drive_source.sweep_done()):
                time.sleep(0.1)
        finally:
            vna.output(False)
            drive_source.output(False)
    
    def prepare_experiment(self, writer, exp_file):
        writer.add_tag(self.tags)
        if exp_file is None:
            writer.backup_file([__file__])
        else:
            writer.backup_file([exp_file, __file__])
        writer.save_text("wiring.md", self.wiring_info)
        writer.save_dict("station_snapshot.json", self.station.snapshot())


