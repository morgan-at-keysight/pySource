"""
instruments
Author: Morgan Allison, Keysight RF/uW Application Engineer
Builds instrument specific classes for each signal generator.
The classes include minimum waveform length/granularity checks, binary
waveform formatting, sequencer length/granularity checks, sample rate
checks, etc. per instrument.
Tested on M8190A, M8195A, M8196A,
N5182B, E8257D, M9383A, N5193A, N5194A
"""

import numpy as np
import math
from pyarbtools import communications
from pyarbtools import error

"""
TODO:
* Bugfix: fix zero/hold behavior on VectorUXG LAN pdw streaming
* Add check to each instrument class to ensure that the correct 
    instrument is connected
* Add a function for IQ adjustments in VSG class
* Add multithreading for waveform download and wfmBuilder
* Separate out configure() into individual methods that update class attributes
* Add a check for PDW length (600k limit?)
* Add a multi-binblockwrite feature for download_wfm in the case of 
    waveform size > 1 GB
"""


def wraparound_calc(length, gran, minLen):
    """
    Computes the number of times to repeat a waveform based on
    generator granularity requirements.
    Args:
        length (int): Length of waveform
        gran (int): Granularity of waveform, determined by signal generator class
        minLen: Minimum wfm length, determined by signal generator class

    Returns:
        (int) Number of repeats required to satisfy gran and minLen requirements

    """

    repeats = 1
    temp = length
    while temp % gran != 0 or temp < minLen:
        temp += length
        repeats += 1
    return repeats


class M8190A(communications.SocketInstrument):
    """Generic class for controlling a Keysight M8190A AWG."""

    def __init__(self, host, port=5025, timeout=10, reset=False):
        super().__init__(host, port, timeout)
        if reset:
            self.write('*rst')
            self.query('*opc?')
            self.write('abort')
        self.res = self.query('trace1:dwidth?').strip().lower()
        self.func1 = self.query('func1:mode?').strip()
        self.func2 = self.query('func2:mode?').strip()
        self.gran = 0
        self.minLen = 0
        self.binMult = 0
        self.binShift = 0
        self.intFactor = 1
        self.idleGran = 0
        self.clkSrc = self.query('frequency:raster:source?').strip().lower()
        self.fs = float(self.query('frequency:raster?').strip())
        self.bbfs = self.fs
        self.refSrc = self.query('roscillator:source?').strip()
        self.refFreq = float(self.query('roscillator:frequency?').strip())
        self.out1 = self.query('output1:route?').strip()
        self.out2 = self.query('output2:route?').strip()
        self.func1 = self.query('func1:mode?').strip()
        self.func2 = self.query('func2:mode?').strip()
        self.cf1 = float(self.query('carrier1:freq?').strip().split(',')[0])
        self.cf2 = float(self.query('carrier2:freq?').strip().split(',')[0])
        self.check_resolution()

    def sanity_check(self):
        """Prints out initialized values."""
        print('Sample rate:', self.fs)
        print('Baseband Sample Rate:', self.bbfs)
        print('Resolution:', self.res)
        print(f'Output path 1: {self.out1}, Output path 2: {self.out2}')
        print(f'Carrier 1: {self.cf1} Hz, Carrier 2: {self.cf2}')
        print(f'Function 1: {self.func1}, Function 2: {self.func2}')
        print('Ref source:', self.refSrc)
        print('Ref frequency:', self.refFreq)

    def configure(self, res='wsp', clkSrc='int', fs=7.2e9, refSrc='axi', refFreq=100e6, out1='dac',
                  out2='dac', amp1=0.65, amp2=0.65, func1='arb', func2='arb', cf1=1e9, cf2=1e9):
        """
        Sets basic configuration for M8190A and populates class attributes accordingly.
        Args:
            res (str): DAC resolution
            clkSrc (str): Sample clock source
            fs (float): Sample clock rate
            refSrc (str): Reference clock source
            refFreq (float): Reference clock frequency
            out1 (str): Output path for channel 1
            out2 (str): Output path for channel 2
            amp1 (float): Output amplitude for channel 1
            amp2 (float): Output amplitude for channel 2
            func1 (str): AWG function for channel 1
            func2 (str): AWG function for channel 2
            cf1 (float): Carrier frequency for channel 1
            cf2 (float): Carrier frequency for channel 2
        """

        self.write('abort')
        self.set_resolution(res)
        self.set_clkSrc(clkSrc)
        self.set_fs(fs)
        self.set_output(1, out1)
        self.set_output(2, out2)
        self.set_amp(1, amp1)
        self.set_amp(2, amp2)
        self.set_func(1, func1)
        self.set_func(2, func2)
        self.set_cf(1, cf1)
        self.set_cf(2, cf2)
        self.set_refSrc(refSrc)
        self.set_refFreq(refFreq)
        self.err_check()

    def set_clkSrc(self, clkSrc):
        """
        Sets and reads clock source parameter.
        Args:
            clkSrc (str): Sample clock source ('int', 'ext')
        """

        if clkSrc.lower() not in ['int', 'ext']:
            raise ValueError("'clkSrc' argument must be 'int' or 'ext'.")
        self.write(f'frequency:raster:source {clkSrc}')
        self.clkSrc = self.query('frequency:raster:source?').strip().lower()

    def set_fs(self, fs):
        """
        Sets and reads sample clock rate.
        Args:
            fs (float): Sample clock rate.
        """

        if not isinstance(fs, float) or fs <= 0:
            raise ValueError('Sample rate must be a positive floating point value.')

        if 'int' in self.clkSrc:
            self.write(f'frequency:raster {fs}')
            self.fs = float(self.query('frequency:raster?').strip())
        else:
            self.write(f'frequency:raster:external {fs}')
            self.fs = float(self.query('frequency:raster:external?').strip())

        self.bbfs = self.fs / self.intFactor

    def set_output(self, ch, out):
        """
        Sets and reads output signal path for a given channel.
        Args:
            ch (int): Channel to be configured
            out (str): Output path for channel ('dac', 'dc', 'ac')
        """

        if out.lower() not in ['dac', 'dc', 'ac']:
            raise ValueError("'out' argument must be 'dac', 'dc', or 'ac'")
        if not isinstance(ch, int) or ch < 1 or ch > 2:
            raise ValueError("'ch' must be 1 or 2.")
        self.write(f'output{ch}:route {out}')
        if ch == 1:
            self.out1 = self.query(f'output{ch}:route?').strip()
        else:
            self.out2 = self.query(f'output{ch}:route?').strip()

    def set_amp(self, ch, amp):
        """
        Sets and reads amplitude (peak to peak value) of a given AWG channel.
        Args:
            ch (int): Channel to be configured.
            amp (float): Output amplitude for channel
        """

        if not isinstance(amp, float) or amp <= 0:
            raise ValueError("'amp' must be a positive floating point value.")
        if not isinstance(ch, int) or ch < 1 or ch > 2:
            raise ValueError("'ch' must be 1 or 2.")

        if ch == 1:
            self.write(f'{self.out1}1:voltage:amplitude {amp}')
            self.amp1 = self.query(f'{self.out1}1:voltage:amplitude?')
        else:
            self.write(f'{self.out2}2:voltage:amplitude {amp}')
            self.amp2 = self.query(f'{self.out2}2:voltage:amplitude?')

    def set_func(self, ch, func):
        """
        Sets and reads function (arb/sequence) of given AWG channel.
        Args:
            ch (int): Channel to be configured
            func (str): AWG function for channel ('arb', 'sts', 'stsc')
        """

        if not isinstance(ch, int) or ch < 1 or ch > 2:
            raise ValueError("'ch' must be 1 or 2.")
        if func not in ['arb', 'sts', 'stsc']:
            raise ValueError("'func' must be 'arb', 'sts' (sequence), or 'stsc' (scenario).")

        self.write(f'func{ch}:mode {func}')
        if ch == 1:
            self.func1 = self.query(f'func{ch}:mode?').strip()
        else:
            self.func2 = self.query(f'func{ch}:mode?').strip()

    def set_cf(self, ch, cf):
        """
        Sets and reads center frequency of a given channel.
        Args:
            ch (int): Channel to be configured
            cf (float): Carrier frequency of channel
        """

        if not isinstance(ch, int) or ch < 1 or ch > 2:
            raise ValueError("'ch' must be 1 or 2.")
        if not isinstance(cf, float) or cf <= 0:
            raise error.SockInstError('Carrier frequency must be a positive floating point value.')
        self.write(f'carrier{ch}:freq {cf}')
        if ch == 1:
            self.cf1 = float(self.query(f'carrier{ch}:freq?').strip().split(',')[0])
        else:
            self.cf2 = float(self.query(f'carrier{ch}:freq?').strip().split(',')[0])

    def set_refSrc(self, refSrc):
        """
        Sets and reads reference clock source.
        Args:
            refSrc (str): Reference clock source ('axi', 'int', 'ext').
        """

        if refSrc.lower() not in ['axi', 'int', 'ext']:
            raise ValueError("'refSrc' argument must be 'axi', 'int', or 'ext'.")

        self.write(f'roscillator:source {refSrc}')
        self.refSrc = self.query('roscillator:source?').strip()

    def set_refFreq(self, refFreq):
        """
        Sets and reads reference frequency.
        Args:
            refFreq (float): Reference clock frequency
        """

        if not isinstance(refFreq, float) or refFreq <= 0:
            raise ValueError('Reference frequency must be a positive floating point value.')

        self.write(f'roscillator:frequency {refFreq}')
        self.refFreq = float(self.query('roscillator:frequency?').strip())

    def set_resolution(self, res='wsp'):
        """
        Sets and reads resolution based on user input.
        Args:
            res (str): DAC resolution of AWG ('wsp', 'wpr', 'intx3', 'intx12', 'intx24', 'intx48')
        """

        if res.lower() not in ['wsp', 'wpr', 'intx3', 'intx12', 'intx24', 'intx48']:
            raise ValueError("res must be 'wsp', 'wpr', 'intx3', 'intx12', 'intx24', or 'intx48'.")

        self.write(f'trace1:dwidth {res}')
        self.res = self.query('trace1:dwidth?').strip().lower()
        self.check_resolution()

    def check_resolution(self):
        """Populates gran, minLen, binMult, binShift, plus intFactor &
        idleGran if using DUC. Values are chosen based on resolution setting."""

        if self.res == 'wpr':
            self.gran = 48
            self.minLen = 240
            self.binMult = 8191
            self.binShift = 2
        elif self.res == 'wsp':
            self.gran = 64
            self.minLen = 320
            self.binMult = 2047
            self.binShift = 4
        elif 'intx' in self.res:
            # Granularity, min length, and binary format are the same for all interpolated modes.
            self.gran = 24
            self.minLen = 120
            self.binMult = 16383
            self.binShift = 1
            self.intFactor = int(self.res.split('x')[-1])
            self.bbfs = self.fs / self.intFactor
            if self.intFactor == 3:
                self.idleGran = 8
            elif self.intFactor == 12:
                self.idleGran = 2
            elif self.intFactor == 24 or self.intFactor == 48:
                self.idleGran = 1
        else:
            raise ValueError("res argument must be 'wsp', 'wpr', 'intx3', 'intx12', 'intx24', or 'intx48'.")

    def download_wfm(self, wfmData, ch=1, name='wfm', wfmFormat='iq', sampleMkr=0, syncMkr=0):
        """
        Defines and downloads a waveform into the segment memory.
        Assigns a waveform name to the segment. Returns segment number.
        Args:
            wfmData (NumPy array): Waveform samples (real or complex floating point values).
            ch (int): Channel to which waveform will be downloaded.
            name (str): Optional name for waveform.
            wfmFormat (str): Format of waveform. ('real', 'iq')
            sampleMkr (int): Index of the beginning of the sample marker.
            syncMkr (int): Index of the beginning of the sync marker.

        Returns:
            (int): Segment number of the downloaded waveform.
        """

        if not isinstance(sampleMkr, int):
            raise TypeError('sampleMkr must be an int.')
        if not isinstance(syncMkr, int):
            raise TypeError('syncMkr must be an int.')

        self.write('abort')
        self.query('*opc?')
        if wfmFormat.lower() == 'iq':
            if wfmData.dtype != np.complex:
                raise TypeError('Invalid wfm type. IQ waveforms must be an array of complex values.')
            else:
                i = self.check_wfm(np.real(wfmData))
                q = self.check_wfm(np.imag(wfmData))

                if sampleMkr:
                    markerData = np.zeros(len(i), dtype=np.int16)
                    markerData[sampleMkr:sampleMkr + 240] = 1
                    i += sampleMkr
                if syncMkr:
                    markerData = np.zeros(len(q), dtype=np.int16)
                    markerData[syncMkr:syncMkr + 240] = 1
                    q += syncMkr

                wfm = self.iq_wfm_combiner(i, q)
                length = len(wfm) / 2
        elif wfmFormat.lower() == 'real':
            wfm = self.check_wfm(wfmData)
            length = len(wfm)
        else:
            raise error.SockInstError('Invalid wfmFormat chosen. Use "iq" or "real".')

        segment = int(self.query(f'trace{ch}:catalog?').strip().split(',')[-2]) + 1
        self.write(f'trace{ch}:def {segment}, {length}')
        self.binblockwrite(f'trace{ch}:data {segment}, 0, ', wfm)
        self.write(f'trace{ch}:name {segment},"{name}_{segment}"')

        return segment

    # def download_iq_wfm(self, i, q, ch=1, name='wfm'):
    #     """Defines and downloads an IQ waveform into the segment memory.
    #     Optionally defines a waveform name. Returns useful waveform
    #     identifier."""
    #
    #     self.write('abort')
    #     self.query('*opc?')
    #     i = self.check_wfm(i)
    #     q = self.check_wfm(q)
    #
    #     iq = self.iq_wfm_combiner(i, q)
    #     length = len(iq) / 2
    #
    #     segment = int(self.query(f'trace{ch}:catalog?').strip().split(',')[-2]) + 1
    #     self.write(f'trace{ch}:def {segment}, {length}')
    #     self.binblockwrite(f'trace{ch}:data {segment}, 0, ', iq)
    #     self.write(f'trace{ch}:name {segment},"{name}_{segment}"')
    #
    #     return segment

    @staticmethod
    def iq_wfm_combiner(i, q):
        """
        Combines i and q wfms into a single interleaved wfm for download to AWG.
        Args:
            i (NumPy array): Array of real waveform samples.
            q (NumPy array): Array of imaginary waveform samples.

        Returns:
            (NumPy array): Array of interleaved IQ values.
        """

        iq = np.empty(2 * len(i), dtype=np.int16)
        iq[0::2] = i
        iq[1::2] = q
        return iq

    def check_wfm(self, wfm):
        """
        Checks minimum size and granularity and returns waveform with
        appropriate binary formatting based on the chosen DAC resolution.

        See pages 273-274 in Keysight M8190A User's Guide (Edition 13.0,
        October 2017) for more info.
        Args:
            wfm (NumPy array): Unscaled/unformatted waveform data.

        Returns:
            (NumPy array): Waveform data that has been scaled and
                formatted appropriately for download to AWG
        """

        self.check_resolution()

        repeats = wraparound_calc(len(wfm), self.gran, self.minLen)
        wfm = np.tile(wfm, repeats)
        rl = len(wfm)
        if rl < self.minLen:
            raise error.AWGError(f'Waveform length: {rl}, must be at least {self.minLen}.')
        rem = rl % self.gran
        if rem != 0:
            raise error.GranularityError(f'Waveform must have a granularity of {self.gran}. Extra samples: {rem}')

        return np.array(self.binMult * wfm, dtype=np.int16) << self.binShift

    def delete_segment(self, wfmID=1, ch=1):
        """
        Deletes specified waveform segment.
        Args:
            wfmID (int): Waveform identifier, used to select waveform to be deleted.
            ch (int): AWG channel from which the segment will be deleted.
        """

        if type(wfmID) != int or wfmID < 1:
            raise error.SockInstError('Segment ID must be a positive integer.')
        if ch not in [1, 2]:
            raise error.SockInstError('Channel must be 1 or 2.')
        self.write('abort')
        self.write(f'trace{ch}:delete {wfmID}')

    def clear_all_wfm(self):
        """Clears all segments from segment memory."""
        self.write('abort')
        self.write('trace1:delete:all')
        self.write('trace2:delete:all')

    def play(self, wfmID=1, ch=1):
        """
        Selects waveform, turns on analog output, and begins continuous playback.
        Args:
            wfmID (int): Waveform identifier, used to select waveform to be played.
            ch (int): AWG channel out of which the waveform will be played.
        """

        self.write('abort')
        self.write(f'trace{ch}:select {wfmID}')
        self.write(f'output{ch}:norm on')
        self.write('init:cont on')
        self.write('init:imm')
        self.query('*opc?')

    def stop(self, ch=1):
        """
        Turns off analog output and stops playback.
        Args:
            ch (int): AWG channel to be deactivated.
        """

        self.write(f'output{ch}:norm off')
        self.write('abort')


# noinspection PyUnusedLocal,PyUnusedLocal
class M8195A(communications.SocketInstrument):
    """Generic class for controlling Keysight M8195A AWG."""

    def __init__(self, host, port=5025, timeout=10, reset=False):
        super().__init__(host, port, timeout)
        if reset:
            self.write('*rst')
            self.query('*opc?')
        self.dacMode = self.query('inst:dacm?').strip()
        self.memDiv = 1
        self.fs = float(self.query('frequency:raster?').strip())
        self.effFs = self.fs / self.memDiv
        self.func = self.query('func:mode?').strip()
        self.refSrc = self.query('roscillator:source?').strip()
        self.refFreq = float(self.query('roscillator:frequency?').strip())
        self.gran = 256
        self.minLen = 1280
        self.binMult = 127
        self.binShift = 0

    def configure(self, dacMode='single', memDiv=1, fs=64e9, refSrc='axi', refFreq=100e6, func='arb'):
        """
        Sets basic configuration for M8195A and populates class attributes accordingly.
        Args:
            dacMode (str): DAC operation mode. ('single', 'dual', 'four', 'marker', 'dcd', 'dcmarker')
            memDiv (int): Clock/memory divider rate. (1, 2, 4)
            fs (float): AWG sample rate.
            refSrc (str): Reference clock source. ('axi', 'int', 'ext')
            refFreq (float): Reference clock frequency.
            func (str): AWG mode, either arb or sequencing. ('arb', 'sts', 'stsc')
        """

        self.set_dacMode(dacMode)
        self.set_memDiv(memDiv)
        self.set_fs(fs)
        self.set_func(func)
        self.set_refSrc(refSrc)
        self.set_refFreq(refFreq)

        self.err_check()

    def set_dacMode(self, dacMode='single'):
        """
        Sets and reads DAC mode for the M8195A
        Args:
            dacMode (str): DAC operation mode. ('single', 'dual', 'four', 'marker', 'dcd', 'dcmarker')
        """

        if dacMode not in ['single', 'dual', 'four', 'marker', 'dcd', 'dcmarker']:
            raise ValueError('INSERT ERRIOR HERE')

        self.write(f'inst:dacm {dacMode}')
        self.dacMode = self.query('inst:dacm?').strip().lower()

    def set_memDiv(self, memDiv=1):
        """
        Sets and reads memory divider rate.
        Args:
            memDiv (int): Clock/memory divider rate. (1, 2, 4)
        """

        if memDiv not in [1, 2, 4]:
            raise ValueError('Memory divider must be 1, 2, or 4.')
        self.write(f'instrument:memory:extended:rdivider div{memDiv}')
        self.memDiv = int(self.query('instrument:memory:extended:rdivider?').strip().split('DIV')[-1])

    def set_fs(self, fs=65e9):
        """
        Sets and reads sample rate.
        Args:
            fs (float): AWG sample rate.
        """

        if not isinstance(fs, float) or fs <= 0:
            raise ValueError('Sample rate must be a positive floating point value.')
        self.write(f'frequency:raster {fs}')
        self.fs = float(self.query('frequency:raster?').strip())
        self.effFs = self.fs / self.memDiv

    def set_func(self, func='arb'):
        """
        Sets and reads AWG function.
        Args:
            func (str): AWG mode, either arb or sequencing. ('arb', 'sts', 'stsc')
        """

        if func.lower() not in ['arb', 'sts', 'stsc']:
            raise ValueError("'func' argument must be 'arb', 'sts', 'stsc'")
        self.write(f'func:mode {func}')
        self.func = self.query('func:mode?').strip()

    def set_refSrc(self, refSrc='axi'):
        """
        Sets and reads reference source.
        Args:
            refSrc (str): Reference clock source. ('axi', 'int', 'ext')
        """

        if refSrc.lower() not in ['axi', 'int', 'ext']:
            raise ValueError("'refSrc' must be 'axi', 'int', or 'ext'")
        self.write(f'roscillator:source {refSrc}')
        self.refSrc = self.query('roscillator:source?').strip()

    def set_refFreq(self, refFreq=100e6):
        """
        Sets and reads reference frequency.
        Args:
            refFreq (float): Reference clock frequency.
        """

        if not isinstance(refFreq, float) or refFreq <= 0:
            raise ValueError('Reference frequency must be a positive floating point value.')
        self.write(f'roscillator:frequency {refFreq}')
        self.refFreq = float(self.query('roscillator:frequency?').strip())

    def sanity_check(self):
        """Prints out initialized values."""
        print('Sample rate:', self.fs)
        print('DAC Mode:', self.dacMode)
        print('Function:', self.func)
        print('Ref source:', self.refSrc)
        print('Ref frequency:', self.refFreq)

    def download_wfm(self, wfmData, ch=1, name='wfm', *args, **kwargs):
        """
        Defines and downloads a waveform into the segment memory.
        Assigns a waveform name to the segment. Returns segment number.
        Args:
            wfmData (NumPy array): Waveform samples (real or complex floating point values).
            ch (int): Channel to which waveform will be downloaded.
            name (str): Optional name for waveform.
            # sampleMkr (int): Index of the beginning of the sample marker.
            # syncMkr (int): Index of the beginning of the sync marker.

        Returns:
            (int): Segment number of the downloaded waveform.
        """

        self.write('abort')
        wfm = self.check_wfm(wfmData)
        length = len(wfmData)

        segment = int(self.query(f'trace{ch}:catalog?').strip().split(',')[-2]) + 1
        self.write(f'trace{ch}:def {segment}, {length}')
        self.binblockwrite(f'trace{ch}:data {segment}, 0, ', wfm)
        self.write(f'trace{ch}:name {segment},"{name}_{segment}"')

        return segment

    def check_wfm(self, wfmData):
        """
        Checks minimum size and granularity and returns waveform with
        appropriate binary formatting.

        See pages 273-274 in Keysight M8195A User's Guide (Edition 13.0,
        October 2017) for more info.
        Args:
            wfmData (NumPy array): Unscaled/unformatted waveform data.

        Returns:
            (NumPy array): Waveform data that has been scaled and
                formatted appropriately for download to AWG
        """

        repeats = wraparound_calc(len(wfmData), self.gran, self.minLen)
        wfm = np.tile(wfmData, repeats)
        rl = len(wfm)
        if rl < self.minLen:
            raise error.AWGError(f'Waveform length: {rl}, must be at least {self.minLen}.')
        if rl % self.gran != 0:
            raise error.GranularityError(f'Waveform must have a granularity of {self.gran}.')

        return np.array(self.binMult * wfm, dtype=np.int8) << self.binShift

    def delete_segment(self, wfmID=1, ch=1):
        """
        Deletes specified waveform segment.
        Args:
            wfmID (int): Waveform identifier, used to select waveform to be deleted.
            ch (int): AWG channel from which the segment will be deleted.
        """

        if type(wfmID) != int or wfmID < 1:
            raise error.SockInstError('Segment ID must be a positive integer.')
        if ch not in [1, 2, 3, 4]:
            raise error.SockInstError('Channel must be 1, 2, 3, or 4.')
        self.write('abort')
        self.write(f'trace{ch}:del {wfmID}')

    def clear_all_wfm(self):
        """Clears all segments from segment memory."""
        self.write('abort')
        for ch in range(1, 5):
            self.write(f'trace{ch}:del:all')

    def play(self, wfmID=1, ch=1):
        """
        Selects waveform, turns on analog output, and begins continuous playback.
        Args:
            wfmID (int): Waveform identifier, used to select waveform to be played.
            ch (int): AWG channel out of which the waveform will be played.
        """

        self.write(f'trace:select {wfmID}')
        self.write(f'output{ch} on')
        self.write('init:cont on')
        self.write('init:imm')

    def stop(self, ch=1):
        """
        Turns off analog output and stops playback.
        Args:
            ch (int): AWG channel to be deactivated.
        """

        self.write(f'output{ch} off')
        self.write('abort')


# noinspection PyUnusedLocal,PyUnusedLocal
class M8196A(communications.SocketInstrument):
    """Generic class for controlling Keysight M8196A AWG."""

    def __init__(self, host, port=5025, timeout=10, reset=False):
        super().__init__(host, port, timeout)
        if reset:
            self.write('*rst')
            self.query('*opc?')
        self.dacMode = self.query('inst:dacm?').strip()
        self.fs = float(self.query('frequency:raster?').strip())
        self.amp = float(self.query('voltage?').strip())
        self.refSrc = self.query('roscillator:source?').strip()
        self.refFreq = float(self.query('roscillator:frequency?').strip())
        self.gran = 128
        self.minLen = 128
        self.maxLen = 524288
        self.binMult = 127
        self.binShift = 0

    def configure(self, dacMode='single', fs=92e9, refSrc='axi', refFreq=100e6):
        """
        Sets basic configuration for M8196A and populates class attributes accordingly.
        Args:
            dacMode (str): DAC operation mode. ('single', 'dual', 'four', 'marker', 'dcmarker')
            fs (float): AWG sample rate.
            refSrc (str): Reference clock source. ('axi', 'int', 'ext')
            refFreq (float): Reference clock frequency.
        """

        # Built-in type and range checking for dacMode, fs, and amplitude
        if not isinstance(fs, float) or fs <= 0:
            raise ValueError('Sample rate must be a positive floating point value.')
        if not isinstance(refFreq, float) or refFreq <= 0:
            raise ValueError('Reference frequency must be a positive floating point value.')

        self.write(f'inst:dacm {dacMode}')
        self.dacMode = self.query('inst:dacm?').strip().lower()

        self.write(f'frequency:raster {fs}')
        self.fs = float(self.query('frequency:raster?').strip())

        # Check for valid refSrc arguments and assign
        if refSrc.lower() not in ['int', 'ext', 'axi']:
            raise error.AWGError('Invalid reference source selection.')
        self.write(f'roscillator:source {refSrc}')
        self.refSrc = self.query('roscillator:source?').strip().lower()

        # Check for presence of external ref signal
        srcAvailable = self.query(f'roscillator:source:check? {refSrc}').strip()
        if not srcAvailable:
            raise error.AWGError('No signal at selected reference source.')

        # Only set ref frequency if using ext ref, int/axi is always 100 MHz
        if self.refSrc == 'ext':
            # Seamlessly manage external clock range selection based on ref freq.
            # Precision clock source
            if 2.3125e9 <= refFreq <= 3e9:
                self.write('roscillator:range rang3')
            # Standard external clock source
            elif 10e6 <= refFreq <= 300e6:
                self.write('roscillator:range rang1')
            # Wide external clock source
            elif 162e6 <= refFreq <= 17e9:
                self.write('roscillator:range rang2')
            else:
                raise error.AWGError('Selected reference clock frequency outside allowable range.')
            self.write(f'roscillator:frequency {refFreq}')
        self.refFreq = float(self.query('roscillator:frequency?').strip())

        self.err_check()

    def sanity_check(self):
        """Prints out initialized values."""
        print('Sample rate:', self.fs)
        print('DAC Mode:', self.dacMode)
        print('Ref source:', self.refSrc)
        print('Ref frequency:', self.refFreq)

    def download_wfm(self, wfmData, ch=1, name='wfm', *args, **kwargs):
        """
        Defines and downloads a waveform into the segment memory.
        Assigns a waveform name to the segment. Returns segment number.
        Args:
            wfmData (NumPy array): Waveform samples (real or complex floating point values).
            ch (int): Channel to which waveform will be downloaded.
            name (str): Optional name for waveform.
            # sampleMkr (int): Index of the beginning of the sample marker.
            # syncMkr (int): Index of the beginning of the sync marker.

        Returns:
            (int): Segment number of the downloaded waveform.
        """

        self.clear_all_wfm()
        wfm = self.check_wfm(wfmData)
        length = len(wfm)

        segment = 1
        self.write(f'trace{ch}:def {segment}, {length}')
        self.binblockwrite(f'trace{ch}:data {segment}, 0, ', wfm)
        self.write(f'trace{ch}:name {segment},"{name}_{segment}"')

        return segment

    def check_wfm(self, wfmData):
        """
        Checks minimum size and granularity and returns waveform with
        appropriate binary formatting.

        See page 132 in Keysight M8196A User's Guide (Edition 2.2,
        March 2018) for more info.
        Args:
            wfmData (NumPy array): Unscaled/unformatted waveform data.

        Returns:
            (NumPy array): Waveform data that has been scaled and
                formatted appropriately for download to AWG
        """

        repeats = wraparound_calc(len(wfmData), self.gran, self.minLen)
        wfm = np.tile(wfmData, repeats)
        rl = len(wfm)
        if rl < self.minLen:
            raise error.AWGError(f'Waveform length: {rl}, must be at least {self.minLen}.')
        if rl > self.maxLen:
            raise error.AWGError(f'Waveform length: {rl}, must be shorter than {self.maxLen}.')
        if rl % self.gran != 0:
            raise error.GranularityError(f'Waveform must have a granularity of {self.gran}.')

        return np.array(self.binMult * wfm, dtype=np.int8) << self.binShift

    def delete_segment(self):
        """Deletes waveform segment"""
        self.clear_all_wfm()

    def clear_all_wfm(self):
        """Clears all segments from segment memory."""
        self.write('abort')
        for ch in range(1, 5):
            self.write(f'trace{ch}:del:all')

    def play(self, ch=1):
        """
        Selects waveform, turns on analog output, and begins continuous playback.
        Args:
            wfmID (int): Waveform identifier, used to select waveform to be played.
            ch (int): AWG channel out of which the waveform will be played.
        """

        self.write(f'output{ch}:state on')
        self.write('init:cont on')
        self.write('init:imm')

    def stop(self, ch=1):
        """
        Turns off analog output and stops playback.
        Args:
            ch (int): AWG channel to be deactivated.
        """

        self.write('abort')
        self.write(f'output{ch}:state off')


class VSG(communications.SocketInstrument):
    def __init__(self, host, port=5025, timeout=10, reset=False):
        """Generic class for controlling the EXG, MXG, PSG, and M938X
        family signal generators."""

        super().__init__(host, port, timeout)
        if reset:
            self.write('*rst')
            self.query('*opc?')
        self.rfState = self.query('output?').strip()
        self.modState = self.query('output:modulation?').strip()
        self.cf = float(self.query('frequency?').strip())
        self.amp = float(self.query('power?').strip())
        self.alcState = self.query('power:alc?')
        self.refSrc = self.query('roscillator:source?').strip()
        self.arbState = self.query('radio:arb:state?').strip()
        self.fs = float(self.query('radio:arb:sclock:rate?').strip())

        if 'int' in self.refSrc.lower():
            self.refFreq = 10e6
        elif 'ext' in self.refSrc.lower():
            self.refFreq = float(self.query('roscillator:frequency:external?').strip())
        elif 'bbg' in self.refSrc.lower():
            if 'M938' not in self.instId:
                self.refFreq = float(self.query('roscillator:frequency:bbg?').strip())
            else:
                raise error.VSGError('Invalid reference source chosen, select \'int\' or \'ext\'.')
        else:
            raise error.VSGError('Unknown refSrc selected.')

        self.minLen = 60
        self.binMult = 32767
        if 'M938' not in self.instId:
            self.iqScale = float(self.query('radio:arb:rscaling?').strip())
            self.gran = 2
        else:
            self.gran = 4

    def configure(self, rfState=1, modState=1, cf=1e9, amp=-20, alcState=0, iqScale=70, refSrc='int', fs=200e6):
        """
        Sets basic configuration for VSG and populates class attributes accordingly.
        Args:
            rfState (int): Turns the RF output on or off. (1, 0)
            modState (int): Turns the baseband modulator on or off. (1, 0)
            cf (float): Sets the generator's carrier frequency.
            amp (int/float): Sets the generator's RF output power.
            alcState (int): Turns the ALC (automatic level control) on or off. (1, 0)
            iqScale (int): Scales the IQ modulator. Default/safe value is 70
            refSrc (str): Sets the reference clock source. ('int', 'ext', 'bbg')
            fs (float): Sets the sample rate of the baseband generator.
        """

        if not isinstance(fs, float) or fs <= 0:
            raise ValueError('Sample rate must be a positive floating point value.')
        if not isinstance(cf, float) or cf <= 0:
            raise ValueError('Carrier frequency must be a positive floating point value.')
        if not isinstance(iqScale, int) or iqScale <= 0 or iqScale > 100:
            raise ValueError('iqScale argument must be an integer between 1 and 100.')
        if not isinstance(amp, int):
            raise ValueError('Amp argument must be an integer.')

        self.write(f'output {rfState}')
        self.rfState = int(self.query('output?').strip())
        self.write(f'output:modulation {modState}')
        self.modState = int(self.query('output:modulation?').strip())
        self.write(f'frequency {cf}')
        self.cf = float(self.query('frequency?').strip())
        self.write(f'power {amp}')
        self.amp = float(self.query('power?').strip())
        self.write(f'power:alc {alcState}')
        self.alcState = int(self.query('power:alc?').strip())
        self.write(f'roscillator:source {refSrc}')
        self.refSrc = self.query('roscillator:source?').strip()
        if 'int' in self.refSrc.lower():
            self.refFreq = 10e6
        elif 'ext' in self.refSrc.lower():
            self.refFreq = float(self.query('roscillator:frequency:external?').strip())
        elif 'bbg' in self.refSrc.lower():
            self.refFreq = float(self.query('roscillator:frequency:bbg?').strip())
        else:
            raise error.VSGError('Unknown refSrc selected.')
        self.write(f'radio:arb:sclock:rate {fs}')
        self.fs = float(self.query('radio:arb:sclock:rate?').strip())

        # M9381/3A don't have an IQ scaling command
        if 'M938' not in self.instId:
            self.write(f'radio:arb:rscaling {iqScale}')
            self.iqScale = float(self.query('radio:arb:rscaling?').strip())

        # Arb state can only be turned on after a waveform has been loaded/selected
        # self.write(f'radio:arb:state {arbState}')
        # self.arbState = self.query('radio:arb:state?').strip()

        self.err_check()

    def sanity_check(self):
        """Prints out initialized values."""
        print('RF State:', self.rfState)
        print('Modulation State:', self.modState)
        print('Center Frequency:', self.cf)
        print('Output Amplitude:', self.amp)
        print('ALC state:', self.alcState)
        print('Reference Source:', self.refSrc)
        print('Internal Arb State:', self.arbState)
        print('Internal Arb Sample Rate:', self.fs)
        if 'M938' not in self.instId:
            print('IQ Scaling:', self.iqScale)

    def download_wfm(self, wfmData, wfmID='wfm'):
        """
        Defines and downloads a waveform into the waveform memory.
        Returns useful waveform identifier.
        Args:
            wfmData (NumPy array): Complex waveform values.
            wfmID (str): Waveform name.

        Returns:
            (str): Useful waveform identifier/name.
        """

        # Adjust endianness for M9381/3A
        if 'M938' in self.instId:
            bigEndian = False
        else:
            bigEndian = True

        self.write('radio:arb:state off')
        self.write('modulation:state off')
        self.arbState = self.query('radio:arb:state?').strip()

        if wfmData.dtype != np.complex:
            raise TypeError('Invalid wfm type. IQ waveforms must be an array of complex values.')
        else:
            i = self.check_wfm(np.real(wfmData), bigEndian=bigEndian)
            q = self.check_wfm(np.imag(wfmData), bigEndian=bigEndian)

            wfm = self.iq_wfm_combiner(i, q)

        # M9381/3A Download Procedure
        if 'M938' in self.instId:
            try:
                self.write(f'memory:delete "{wfmID}"')
                self.query('*opc?')
                self.write(f'mmemory:delete "C:\\Temp\\{wfmID}"')
                self.query('*opc?')
                self.err_check()
            except error.SockInstError:
                # print('Waveform doesn\'t exist, skipping delete operation.')
                pass
            self.binblockwrite(f'mmemory:data "C:\\Temp\\{wfmID}",', wfm)
            self.write(f'memory:copy "C:\\Temp\\{wfmID}","{wfmID}"')
        # EXG/MXG/PSG Download Procedure
        else:
            self.binblockwrite(f'mmemory:data "WFM1:{wfmID}", ', wfm)
            self.write(f'radio:arb:waveform "WFM1:{wfmID}"')

        return wfmID

    @staticmethod
    def iq_wfm_combiner(i, q):
        """
        Combines i and q wfms into a single interleaved wfm for download to generator.
        Args:
            i (NumPy array): Array of real waveform samples.
            q (NumPy array): Array of imaginary waveform samples.

        Returns:
            (NumPy array): Array of interleaved IQ values.
        """

        iq = np.empty(2 * len(i), dtype=np.int16)
        iq[0::2] = i
        iq[1::2] = q
        return iq

    def check_wfm(self, wfm, bigEndian=True):
        """
        Checks minimum size and granularity and returns waveform with
        appropriate binary formatting. Note that sig gens expect big endian
        byte order.

        See pages 205-256 in Keysight X-Series Signal Generators Programming
        Guide (November 2014 Edition) for more info.
        Args:
            wfm (NumPy array): Unscaled/unformatted waveform data.
            bigEndian (bool): Determines whether waveform is big endian.

        Returns:
            (NumPy array): Waveform data that has been scaled and
                formatted appropriately for download to AWG
        """

        repeats = wraparound_calc(len(wfm), self.gran, self.minLen)
        wfm = np.tile(wfm, repeats)
        rl = len(wfm)
        if rl < self.minLen:
            raise error.VSGError(f'Waveform length: {rl}, must be at least {self.minLen}.')
        if rl % self.gran != 0:
            raise error.GranularityError(f'Waveform must have a granularity of {self.gran}.')

        if bigEndian:
            return np.array(self.binMult * wfm, dtype=np.int16).byteswap()
        else:
            return np.array(self.binMult * wfm, dtype=np.int16)

    def delete_wfm(self, wfmID):
        """
        Stops output and deletes specified waveform.
        Args:
            wfmID (str): Name of waveform to be deleted.
        """

        self.stop()
        if 'M938' in self.instId:
            self.write(f'memory:delete "{wfmID}"')
        else:
            self.write(f'memory:delete "WFM1:{wfmID}"')
        self.err_check()

    def clear_all_wfm(self):
        """Stops output and deletes all iq waveforms."""
        self.stop()
        if 'M938' in self.instId:
            """UNTESTED PLEASE TEST"""
            self.write('memory:delete:all')
        else:
            self.write('mmemory:delete:wfm')
        self.err_check()

    def play(self, wfmID='wfm'):
        """
        Selects waveform and activates arb mode, RF output, and modulation.
        Args:
            wfmID (str): Waveform identifier, used to select waveform to be played.
        """

        if 'M938' in self.instId:
            self.write(f'radio:arb:waveform "{wfmID}"')
        else:
            self.write(f'radio:arb:waveform "WFM1:{wfmID}"')

        self.write('radio:arb:state on')
        self.arbState = self.query('radio:arb:state?').strip()
        self.write('output on')
        self.rfState = self.query('output?').strip()
        self.write('output:modulation on')
        self.modState = self.query('output:modulation?').strip()
        self.err_check()

    def stop(self):
        """Dectivates arb mode, RF output, and modulation."""
        self.write('radio:arb:state off')
        self.arbState = self.query('radio:arb:state?').strip()
        self.write('output off')
        self.rfState = self.query('output?').strip()
        self.write('output:modulation off')
        self.modState = self.query('output:modulation?').strip()


# noinspection PyUnusedLocal
class AnalogUXG(communications.SocketInstrument):
    """Generic class for controlling the N5193A Analog UXG agile signal generators."""

    def __init__(self, host, port=5025, timeout=10, reset=False, clearMemory=False):
        super().__init__(host, port, timeout)
        if reset:
            self.write('*rst')
            self.query('*opc?')
        # Clear all files
        if clearMemory:
            self.clear_memory()
        self.host = host
        self.rfState = self.query('output?').strip()
        self.modState = self.query('output:modulation?').strip()
        self.streamState = self.query('stream:state?').strip()
        self.cf = float(self.query('frequency?').strip())
        self.amp = float(self.query('power?').strip())
        self.refSrc = self.query('roscillator:source?').strip()
        self.refFreq = 10e6
        self.mode = self.query('instrument?').strip()
        self.binMult = 32767

        # Stream state should be turned off until streaming is needed.
        self.write('stream:state off')
        self.streamState = self.query('stream:state?').strip()

        # Set up separate socket for LAN PDW streaming
        self.lanStream = communications.socket.socket(
            communications.socket.AF_INET, communications.socket.SOCK_STREAM)
        self.lanStream.setblocking(False)
        self.lanStream.settimeout(timeout)
        # Can't connect until LAN streaming is turned on
        # self.lanStream.connect((host, 5033))

    def configure(self, rfState=0, modState=0, cf=1e9, amp=-20):
        """
        Sets the basic configuration for the UXG and populates class
        attributes accordingly. It should be called any time these
        settings are changed (ideally once directly after creating the
        UXG object).
        Args:
            rfState (int): Turns the RF output on or off. (1, 0)
            modState (int): Turns the modulator on or off. (1, 0)
            cf (float): Sets the generator's carrier frequency.
            amp (int/float): Sets the generator's RF output power.
            mode (str): Selects the instrument's operating mode. ('normal', 'list', 'fcwswitching', 'streaming', 'vlo')
        """

        if not isinstance(cf, float) or cf <= 0:
            raise ValueError('Carrier frequency must be a positive floating point value.')
        if not isinstance(amp, int):
            raise ValueError('Amp argument must be an integer.')

        self.write(f'output {rfState}')
        self.rfState = self.query('output?').strip()
        self.write(f'output:modulation {modState}')
        self.modState = self.query('output:modulation?').strip()
        self.write(f'frequency {cf}')
        self.cf = float(self.query('frequency?').strip())
        self.write(f'power {amp}')
        self.amp = float(self.query('power?').strip())

        self.write(f'instrument {mode}')
        self.mode = self.query('instrument?').strip()

        self.err_check()

    def sanity_check(self):
        """Prints out initialized values."""
        print('RF State:', self.rfState)
        print('Modulation State:', self.modState)
        print('Center Frequency:', self.cf)
        print('Output Amplitude:', self.amp)
        print('Reference source:', self.refSrc)
        print('Instrument mode:', self.mode)
        self.err_check()

    def open_lan_stream(self):
        """Open connection to port 5033 for LAN streaming to the UXG."""
        self.write('stream:state on')
        self.query('*opc?')
        self.lanStream.connect((self.host, 5033))
        self.lanStream.settimeout(1)

    def close_lan_stream(self):
        """Close LAN streaming port."""
        self.lanStream.shutdown(communications.socket.SHUT_RDWR)
        self.lanStream.close()

    @staticmethod
    def convert_to_floating_point(inputVal, exponentOffset, mantissaBits, exponentBits):
        """
        THIS FUNCTION WAS NOT WRITTEN BY THE AUTHOR
        Computes modified floating point value represented by specified
        floating point parameters.
        fp = gain * mantissa^mantissaExponent * 2^exponentOffset
        Args:
            inputVal:
            exponentOffset:
            mantissaBits:
            exponentBits:

        Returns:
            Floating point value corresponding to passed parameters
        """

        # Error check largest number that can be represented in specified number of bits
        maxExponent = int((1 << exponentBits) - 1)
        maxMantissa = np.uint32(((1 << mantissaBits) - 1))

        exponent = int(math.floor(((math.log(inputVal) / math.log(2)) - exponentOffset)))
        # mantissa = 0

        if exponent > maxExponent:
            # Too big to represent
            exponent = maxExponent
            mantissa = maxMantissa
        elif exponent >= 0:
            mantissaScale = int((1 << mantissaBits))
            effectiveExponent = int(exponentOffset + exponent)
            # ldexp(X, Y) is the same as matlab pow2(X, Y) = > X * 2 ^ Y
            mantissa = np.uint32((((math.ldexp(inputVal, - effectiveExponent) - 1) * mantissaScale) + 0.5))
            if mantissa > maxMantissa:
                # Handle case where rounding causes the mantissa to overflow
                if exponent < maxExponent:
                    # Still representable
                    mantissa = 0
                    exponent += 1
                else:
                    # Handle slightly-too-big to represent case
                    mantissa = maxMantissa
        else:
            # Too small to represent
            mantissa = 0
            exponent = 0
        return ((np.uint32(exponent)) << mantissaBits) | mantissa

    @staticmethod
    def closest_m_2_n(inputVal, mantissaBits, exponent_bits):
        """
        THIS FUNCTION WAS NOT WRITTEN BY THE AUTHOR
        Converts the specified value to the hardware representation in Mantissa*2^Exponent form
        Args:
            inputVal:
            mantissaBits:
            exponent_bits:

        Returns:

        """

        success = True
        # exponent = 0
        # mantissa = 0
        maxMantissa = np.uint32((1 << mantissaBits) - 1)
        # inputVal <= mantissa max inputVal have exponent=0
        if inputVal < (maxMantissa + 0.5):
            exponent = 0
            mantissa = np.uint32((inputVal + 0.5))
            if mantissa > maxMantissa:
                mantissa = maxMantissa
        else:  # exponent > 0 (for value_ins that will have exponent>0 after rounding)
            # find exponent
            mantissaOut, possibleExponent = math.frexp(inputVal)
            possibleExponent -= mantissaBits
            # determine mantissa
            fracMantissa = float(inputVal / (1 << possibleExponent))
            # round to next N if that is closer
            if fracMantissa > (maxMantissa + 0.5 - 1e-9):
                mantissa = 1 << (mantissaBits - 1)
                possibleExponent += 1
            else:  # round mantissa to nearest
                mantissa = np.uint32((fracMantissa + 0.5))
                # do not exceed maximum mantissa
                if mantissa > maxMantissa:
                    mantissa = maxMantissa
            exponent = np.uint32(possibleExponent)

        return success, exponent, mantissa

    def chirp_closest_m_2_n(self, chirpRate, chirpRateRes=21.822):
        """
        THIS FUNCTION WAS NOT WRITTEN BY THE AUTHOR
        Convert the specified value to the hardware representation in Mantissa*2^Exponent form for Chirp parameters
        NOTE: I am not sure why the conversion factor of 21.82 needs to be there, but the math works out perfectly
        Args:
            chirpRate:
            chirpRateRes:

        Returns:

        """

        output = np.uint32(0)
        mantissaBits = 13
        exponentBits = 4

        mantissaMask = np.uint32((1 << mantissaBits) - 1)
        # convert to clocks
        chirpValue = float(chirpRate) / float(chirpRateRes)
        success, exponent, mantissa = self.closest_m_2_n(chirpValue, mantissaBits, exponentBits)
        # compensate for exponent being multiplied by 2
        if exponent & 0x01:
            exponent += 1
            exponent >>= 1
            mantissa = np.uint32(mantissa / 2)
        else:
            exponent >>= 1
        if success:
            # print(exponent)
            # print(mantissaBits)
            # print(mantissa)
            # print(mantissaMask)
            output = np.uint32((exponent << mantissaBits) | (mantissa & mantissaMask))

        return output

    def bin_pdw_builder(self, operation=0, freq=1e9, phase=0, startTimeSec=0, width=0, power=1, markers=0,
                        pulseMode=2, phaseControl=0, bandAdjust=0, chirpControl=0, code=0,
                        chirpRate=0, freqMap=0):
        """
        This function builds a single format-1 PDW from a list of parameters.

        See User's Guide>Streaming Use>PDW Definitions section of
        Keysight UXG X-Series Agile Signal Generator Online Documentation
        http://rfmw.em.keysight.com/wireless/helpfiles/n519xa/n519xa.htm
        Args:
            operation (int): Specifies the operation of the PDW. (0-none, 1-first PDW, 2-last PDW)
            freq (float): CW frequency of PDW.
            phase (float): Phase of CW frequency of PDW.
            startTimeSec (float): Start time of the 50% rising edge power.
            width (float): Width of the pulse from 50% rise power to 50% fall power.
            power (float): Linear scaling of the output in Vrms. (basically just leave this at 1)
            markers (int): Bit mask input of active markers (e.g. to activate marker 3, send the number 4, which is 0100 in binary).
            pulseMode (int): Configures pulse mode. (0-CW, 1-RF off, 2-Pulse enabled)
            phaseControl (int): Switches between phase mode. (0-coherent, 1-continuous)
            bandAdjust (int): Configures band adjustment criteria. (0-CW switch pts, 1-upper band, 2-lower band).
            chirpControl (int): Configures chirp shape. (0-stiched ramp, 1-triangle, 2-ramp)
            code (int): Selects hard-coded frequency/phase coding table index.
            chirpRate (float): Chirp rate in Hz/us.
            freqMap (int): Selects frequency band map. (0-A, 6-B)
        Returns:
            (NumPy array): Single PDW that can be used to build a PDW file or streamed directly to the UXG.
        """

        pdwFormat = 1
        _freq = int(freq * 1024 + 0.5)
        if 180 < phase <= 360:
            phase -= 360
        _phase = int(phase * 4096 / 360 + 0.5)
        _startTimePs = int(startTimeSec * 1e12)
        _widthNs = int(width * 1e9)
        _power = self.convert_to_floating_point(math.pow(10, power / 20), -26, 10, 5)
        _chirpRate = self.chirp_closest_m_2_n(chirpRate)

        # Build PDW
        pdw = np.zeros(7, dtype=np.uint32)
        # Word 0: Mask pdw format (3 bits), operation (2 bits), and the lower 27 bits of freq
        pdw[0] = (pdwFormat | operation << 3 | (_freq << 5 & 0xFFFFFFFF))
        # Word 1: Mask the upper 20 bits (47 - 27) of freq and phase (12 bits)
        pdw[1] = (_freq >> 27 | _phase << 20) & 0xFFFFFFFF
        # Word 2: Lower 32 bits of startTimePs
        pdw[2] = _startTimePs & 0xFFFFFFFF
        # Word 3: Upper 32 bits of startTimePS
        pdw[3] = (_startTimePs & 0xFFFFFFFF00000000) >> 32
        # Word 4: Pulse Width (32 bits)
        pdw[4] = _widthNs
        # Word 5: Mask power (15 bits), markers (12 bits), pulseMode (2 bits), phaseControl (1 bit), and bandAdjust (2 bits)
        pdw[5] = _power | markers << 15 | pulseMode << 27 | phaseControl << 29 | bandAdjust << 30
        # Word 6: Mask wIndex (16 bits), 12 reserved bits, and wfmMkrMask (4 bits)
        pdw[6] = chirpControl | code << 3 | _chirpRate << 12 | freqMap << 29

        return pdw

    # noinspection PyRedundantParentheses
    def bin_pdw_file_builder(self, pdwList):
        """
        Builds a binary PDW file with a padding block to ensure the
        PDW section begins at an offset of 4096 bytes (required by UXG).

        See User's Guide>Streaming Use>PDW File Format section of
        Keysight UXG X-Series Agile Signal Generator Online Documentation
        http://rfmw.em.keysight.com/wireless/helpfiles/n519xa/n519xa.htm
        Args:
            pdwList (list): List of lists. Each inner list contains a single
        pulse descriptor word.

        Returns:
            (bytes): Binary data that contains a full PDW file that can
                be downloaded to and played out of the UXG.
        """

        # Header section, all fixed values
        fileId = b'STRM'
        version = (1).to_bytes(4, byteorder='little')
        # No reason to have > one 4096 byte offset to PDW data.
        offset = ((1 << 1) & 0x3fffff).to_bytes(4, byteorder='little')
        magic = b'KEYS'
        res0 = (0).to_bytes(16, byteorder='little')
        flags = (0).to_bytes(4, byteorder='little')
        uniqueId = (0).to_bytes(4, byteorder='little')
        dataId = (16).to_bytes(4, byteorder='little')
        res1 = (0).to_bytes(4, byteorder='little')
        header = [fileId, version, offset, magic, res0, flags, uniqueId, dataId, res1]

        # Padding block, all fixed values
        padBlockId = (1).to_bytes(4, byteorder='little')
        res3 = (0).to_bytes(4, byteorder='little')
        size = (4016).to_bytes(8, byteorder='little')
        # 4016 bytes of padding ensures that the first PDw begins @ byte 4097
        padData = (0).to_bytes(4016, byteorder='little')
        padding = [padBlockId, res3, size, padData]

        # PDW block
        pdwBlockId = (16).to_bytes(4, byteorder='little')
        res4 = (0).to_bytes(4, byteorder='little')
        pdwSize = (0xffffffffffffffff).to_bytes(8, byteorder='little')
        pdwBlock = [pdwBlockId, res4, pdwSize]

        # Build PDW file from header, padBlock, pdwBlock, and PDWs
        pdwFile = header + padding + pdwBlock
        pdwFile += [self.bin_pdw_builder(*p) for p in pdwList]
        pdwFile += [(0).to_bytes(24, byteorder='little')]
        # Convert arrays of data to a single byte-type variable
        pdwFile = b''.join(pdwFile)

        self.err_check()

        return pdwFile

    def download_bin_pdw_file(self, pdwFile, pdwName='wfm'):
        """
        Downloads binary PDW file to PDW directory in UXG.
        Args:
            pdwFile (bytes): Binary data containing PDW file, generally
                created by the bin_pdw_file_builder() method.
            pdwName (str): Name of PDW file.
        """

        self.binblockwrite(f'memory:data "/USER/PDW/{pdwName}",', pdwFile)
        self.err_check()

    def stream_play(self, pdwID='pdw'):
        """
        Assigns pdw/windex, activates RF output, modulation, and
        streaming mode, and triggers streaming output.
        Args:
            pdwID (str): Name of PDW file used as the source of the
                streaming data.
        """

        self.write('stream:source file')
        self.write(f'stream:source:file:name "{pdwID}"')
        self.err_check()

        # Activate streaming, and send trigger command.
        self.write('output:modulation on')
        self.modState = self.query('output:modulation?').strip()
        self.write('source:stream:state on')
        self.err_check()
        self.streamState = self.query('stream:state?').strip()
        self.err_check()
        self.write('stream:trigger:play')

    def stream_stop(self):
        """Deactivates RF output, modulation, and streaming mode."""
        self.write('output off')
        self.rfState = self.query('output?').strip()
        self.write('output:modulation off')
        self.modState = self.query('output:modulation?').strip()
        self.write('stream:state off')
        self.streamState = self.query('stream:state?').strip()
        self.err_check()


class VectorUXG(communications.SocketInstrument):
    """Generic class for controlling the N5194A + N5193A (Vector + Analog)
    UXG agile signal generators."""

    def __init__(self, host, port=5025, timeout=10, reset=False, clearMemory=False, errCheck=True):
        super().__init__(host, port, timeout)
        if reset:
            self.write('*rst')
            self.query('*opc?')

        self.host = host
        self.rfState = self.query('output?').strip()
        self.modState = self.query('output:modulation?').strip()
        self.arbState = self.query('radio:arb:state?').strip()
        self.streamState = self.query('stream:state?').strip()
        self.cf = float(self.query('frequency?').strip())
        self.amp = float(self.query('power?').strip())
        self.iqScale = float(self.query('radio:arb:rscaling?').strip())
        self.refSrc = self.query('roscillator:source?').strip()
        self.refFreq = 10e6
        self.mode = self.query('instrument:select?').strip()
        self.fs = float(self.query('radio:arb:sclock:rate?').strip())
        self.gran = int(self.query('radio:arb:information:quantum?').strip())
        self.minLen = int(self.query('radio:arb:information:slength:minimum?').strip())
        self.binMult = 32767
        self.errCheck = errCheck

        # Clear all waveform, pdw, and windex files
        if clearMemory:
            self.clear_all_wfm()

        # Arb state can only be turned on after a waveform has been loaded/selected.
        self.write('radio:arb:state off')
        self.arbState = self.query('radio:arb:state?').strip()

        # Set up separate socket for LAN PDW streaming
        self.lanStream = communications.socket.socket(
            communications.socket.AF_INET, communications.socket.SOCK_STREAM)
        self.lanStream.setblocking(False)
        self.lanStream.settimeout(timeout)
        # Can't connect until LAN streaming is turned on
        # self.lanStream.connect((host, 5033))

    def configure(self, rfState=0, modState=0, cf=1e9, amp=-20, iqScale=70):
        """
        Sets the basic configuration for the UXG and populates class
        attributes accordingly. It should be called any time these
        settings are changed (ideally once directly after creating the
        UXG object).
        Args:
            rfState (int): Turns the RF output on or off. (1, 0)
            modState (int): Turns the modulator on or off. (1, 0)
            cf (float): Sets the generator's carrier frequency.
            amp (int/float): Sets the generator's RF output power.
            iqScale (int): Scales the IQ modulator. Default/safe value is 70
        """

        if not isinstance(cf, float) or cf <= 0:
            raise ValueError('Carrier frequency must be a positive floating point value.')
        if not isinstance(amp, int):
            raise ValueError('Amp argument must be an integer.')
        if not isinstance(iqScale, int) or iqScale <= 0 or iqScale > 100:
            raise ValueError('iqScale argument must be an integer between 1 and 100.')

        self.write(f'output {rfState}')
        self.rfState = self.query('output?').strip()
        self.write(f'output:modulation {modState}')
        self.modState = self.query('output:modulation?').strip()
        self.write(f'frequency {cf}')
        self.cf = float(self.query('frequency?').strip())
        self.write(f'power {amp}')
        self.amp = float(self.query('power?').strip())
        self.write(f'radio:arb:rscaling {iqScale}')
        self.iqScale = float(self.query('radio:arb:rscaling?').strip())

        if self.errCheck:
            self.err_check()

    def stream_configure(self, source='file', trigState=True, trigSource='bus', trigInPort=None, trigPeriod=1e-3, trigOutPort=None):
        """
        Configures streaming on the UXG.
        Args:
            source (str): Selects the streaming source. ('file', 'lan')
            trigState (bool): Configures trigger state. (True, False)
            trigSource (str): Selects trigger source. ('key', 'bus', 'external', 'timer')
            trigInPort (int): Selects trigger input port. (1-10)
            trigPeriod (float): Sets period for timer trigger.
            trigOutPort (int): Selects trigger output port. (1-10)
        """

        if source.lower() not in ['file', 'lan']:
            raise error.UXGError('Invalid stream source selected. Use "file" or "lan"')

        self.write(f'stream:source {source}')

        if trigState:
            if trigSource.lower() not in ['key', 'bus', 'external', 'timer']:
                raise error.UXGError('Invalid trigger source selected. Use "key", "bus", "external", or "timer"')
            if trigInPort == trigOutPort and trigInPort and trigOutPort:
                raise error.UXGError('Conflicting trigger ports. trigInPort and trigOutPort must be unique.')
            self.write('stream:trigger:play:file:type:continuous:type trigger')
            self.write(f'stream:trigger:play:source {trigSource}')

            if trigSource.lower() == 'external':
                if trigInPort:
                    if trigInPort < 1 or trigInPort > 10:
                        raise error.UXGError('trigInPort must be an integer between 1 and 10.')
                    self.write(f'trigger:play:external:source trigger{trigInPort}')
            elif trigSource.lower() == 'timer':
                if trigPeriod < 48e-9 or trigPeriod > 34:
                    raise error.UXGError('Invalid trigPeriod')
                self.write(f'trigger:timer {trigPeriod}')

        if trigOutPort:
            if trigOutPort < 1 or trigOutPort > 10:
                raise error.UXGError('trigOutPort must be an integer between 1 and 10.')
            self.write('stream:markers:pdw1:mode stime')
            self.write(f'rout:trigger{trigOut}:output pmarker1')

    def sanity_check(self):
        """Prints out initialized values."""
        print('RF State:', self.rfState)
        print('Modulation State:', self.modState)
        print('Center Frequency:', self.cf)
        print('Output Amplitude:', self.amp)
        print('Reference source:', self.refSrc)
        print('Internal Arb Sample Rate:', self.fs)
        print('IQ Scaling:', self.iqScale)
        if self.errCheck:
            self.err_check()

    def open_lan_stream(self):
        """Open connection to port 5033 for LAN streaming to the UXG."""
        self.write('stream:state on')
        self.query('*opc?')
        self.lanStream.connect((self.host, 5033))

    def close_lan_stream(self):
        """Close LAN streaming port."""
        self.lanStream.shutdown(communications.socket.SHUT_RDWR)
        self.lanStream.close()

    @staticmethod
    def bin_pdw_builder_3(operation=0, freq=1e9, phase=0, startTimeSec=0, width=10e-6,
                        maxPower=0, markers=0, power=0, phaseControl=0, rfOff=0, autoBlank=0,
                        zeroHold=0, loLead=0, wfmMkrMask=0, wIndex=0):
        """
        This function builds a single format-3 PDW from a list of parameters.

        See User's Guide>Streaming Use>PDW Definitions section of
        Keysight UXG X-Series Agile Vector Adapter Online Documentation
        http://rfmw.em.keysight.com/wireless/helpfiles/n519xa-vector/n519xa-vector.htm
        Args:
            operation (int): Specifies the operation of the PDW. (0-none, 1-first PDW, 2-last PDW)
            freq (float): CW frequency of PDW.
            phase (float): Phase of CW frequency of PDW.
            startTimeSec (float): Start time of the 50% rising edge power.
            width (float): Width of the pulse from 50% rise power to 50% fall power.
            maxPower (float): Max output power in dBm.
            markers (int): Enables or disables PDW markers via bit masking. (e.g. to activate marker 3, send the number 4, which is 0100 in binary).
            power (float): Sets power for individual PDW.
            phaseControl (int): Switches between phase mode. (0-coherent, 1-continuous)
            rfOff (int): Activates or deactivates RF Off mode. (0-RF on, 1-RF off). I know, the nomenclature here is TRASH.
            autoBlank (int): Activates blanking. (0-no blanking, 1-blanking)
            zeroHold (int): Selects zero/hold behavior. (0-zero, 1-hold last value)
            loLead (float): Specifies how long before the PDW start time to begin switching LO.
            wfmMkrMask (int): Enables or disables waveform markers via bit masking. (e.g. to activate marker 3, send the number 4, which is 0100 in binary).
            wIndex (int): Index of the IQ waveform to be assigned to the PDW.

        Returns:
            (NumPy array): Single PDW that can be used to build a PDW file or streamed directly to the UXG.
        """

        pdwFormat = 3
        _freq = int(freq * 1024 + 0.5)
        _phase = int(phase * 4096 / 360 + 0.5)
        _startTimePs = int(startTimeSec * 1e12)
        # you multiplied this by 2, it's probably not going to work.
        _pulseWidthPs = int(width * 1e12 * 2)
        _maxPower = int((maxPower + 140) / 0.005 + 0.5)
        _power = int((power + 140) / 0.005 + 0.5)
        _loLead = int(loLead / 4e-9)
        _newWfm = 1
        _wfmType

        # Build PDW
        pdw = np.zeros(11, dtype=np.uint32)
        # Word 0: Mask pdw format (3 bits), operation (2 bits), and the lower 27 bits of freq
        pdw[0] = (pdwFormat | operation << 3 | _freq << 5) & 0xFFFFFFFF
        # Word 1: Mask the upper 20 bits (47 - 27) of freq and phase (12 bits)
        pdw[1] = (_freq >> 27 | _phase << 20) & 0xFFFFFFFF
        # Word 2: Lower 32 bits of startTimePs
        pdw[2] = _startTimePs & 0xFFFFFFFF
        # Word 3: Upper 32 bits of startTimePS
        pdw[3] = (_startTimePs & 0xFFFFFFFF00000000) >> 32
        # Word 4: Lower 32 bits of Pulse width (37 bits)
        pdw[4] = _pulseWidthPs & 0xFFFFFFFF
        # Word 5: Upper 5 bits of Pulse width, max power (15 bits), markers (12 bits)
        pdw[5] = (_pulseWidthPs & 0x1F00000000) >> 32 | _maxPower << 5 | markers << 20
        # Word 5: Power (15 bits), phase mode (1), RF off (1), auto blank (1), new wfm (1),
        # zero/hold (1), lo lead (8), marker mask (4)
        pdw[6] = _power | phaseControl << 15 | rfOff << 16 | autoBlank << 17 | _newWfm << 18 | zeroHold << 19 | _loLead << 20 | wfmMkrMask << 28
        # Word 7: Reserved (8), Wfm type (2), index (16) reserved (
        pdw[7] = _wfmType << 8 | wIndex << 10

        return pdw

    @staticmethod
    def bin_pdw_builder(operation, freq, phase, startTimeSec, power, markers,
                        phaseControl, rfOff, wIndex, wfmMkrMask):
        """
        This function builds a single format-1 PDW from a list of parameters.

        See User's Guide>Streaming Use>PDW Definitions section of
        Keysight UXG X-Series Agile Vector Adapter Online Documentation
        http://rfmw.em.keysight.com/wireless/helpfiles/n519xa-vector/n519xa-vector.htm
        Args:
            operation (int): Specifies the operation of the PDW. (0-none, 1-first PDW, 2-last PDW)
            freq (float): CW frequency of PDW.
            phase (float): Phase of CW frequency of PDW.
            startTimeSec (float): Start time of the 50% rising edge power.
            power (float): Sets power for individual PDW.
            markers (int): Enables or disables PDW markers via bit masking. (e.g. to activate marker 3, send the number 4, which is 0100 in binary).
            phaseControl (int): Switches between phase mode. (0-coherent, 1-continuous)
            rfOff (int): Activates or deactivates RF Off mode. (0-RF on, 1-RF off). I know, the nomenclature here is TRASH.
            wIndex (int): Index of the IQ waveform to be assigned to the PDW.
            wfmMkrMask (int): Enables or disables waveform markers via bit masking. (e.g. to activate marker 3, send the number 4, which is 0100 in binary).

        Returns:
            (NumPy array): Single PDW that can be used to build a PDW file or streamed directly to the UXG.
        """

        # Format 1 PDWs are deprecated
        pdwFormat = 1
        _freq = int(freq * 1024 + 0.5)
        _phase = int(phase * 4096 / 360 + 0.5)
        _startTimePs = int(startTimeSec * 1e12)
        _power = int((power + 140) / 0.005 + 0.5)

        # Build PDW
        pdw = np.zeros(6, dtype=np.uint32)
        # Word 0: Mask pdw format (3 bits), operation (2 bits), and the lower 27 bits of freq
        pdw[0] = (pdwFormat | operation << 3 | _freq << 5) & 0xFFFFFFFF
        # Word 1: Mask the upper 20 bits (47 - 27) of freq and phase (12 bits)
        pdw[1] = (_freq >> 27 | _phase << 20) & 0xFFFFFFFF
        # Word 2: Lower 32 bits of startTimePs
        pdw[2] = _startTimePs & 0xFFFFFFFF
        # Word 3: Upper 32 bits of startTimePS
        pdw[3] = (_startTimePs & 0xFFFFFFFF00000000) >> 32
        # Word 4: Mask power (15 bits), markers (12 bits), phaseControl (1 bit), and rfOff (1 bit)
        pdw[4] = _power | markers << 15 | phaseControl << 27 | rfOff << 28
        # Word 5: Mask wIndex (16 bits), 12 reserved bits, and wfmMkrMask (4 bits)
        pdw[5] = wIndex | 0b000000000000 << 16 | wfmMkrMask << 28

        return pdw

    # noinspection PyRedundantParentheses
    def bin_pdw_file_builder(self, pdwList):
        """
        Builds a binary PDW file with a padding block to ensure the
        PDW section begins at an offset of 4096 bytes (required by UXG).

        See User's Guide>Streaming Use>PDW Definitions section of
        Keysight UXG X-Series Agile Vector Adapter Online Documentation
        http://rfmw.em.keysight.com/wireless/helpfiles/n519xa-vector/n519xa-vector.htm
        Args:
            pdwList (list): List of lists. Each inner list contains a single
        pulse descriptor word.

        Returns:
            (bytes): Binary data that contains a full PDW file that can
                be downloaded to and played out of the UXG.
        """

        # Header section, all fixed values
        fileId = b'STRM'
        version = (1).to_bytes(4, byteorder='little')
        # No reason to have > one 4096 byte offset to PDW data.
        offset = ((1 << 1) & 0x3fffff).to_bytes(4, byteorder='little')
        magic = b'KEYS'
        res0 = (0).to_bytes(16, byteorder='little')
        flags = (0).to_bytes(4, byteorder='little')
        uniqueId = (0).to_bytes(4, byteorder='little')
        dataId = (64).to_bytes(4, byteorder='little')
        res1 = (0).to_bytes(4, byteorder='little')
        header = [fileId, version, offset, magic, res0, flags, uniqueId, dataId, res1]

        # Padding block, all fixed values
        padBlockId = (1).to_bytes(4, byteorder='little')
        res3 = (0).to_bytes(4, byteorder='little')
        size = (4016).to_bytes(8, byteorder='little')
        # 4016 bytes of padding ensures that the first PDw begins @ byte 4097
        padData = (0).to_bytes(4016, byteorder='little')
        padding = [padBlockId, res3, size, padData]

        # PDW block
        pdwBlockId = (16).to_bytes(4, byteorder='little')
        res4 = (0).to_bytes(4, byteorder='little')
        pdwSize = (0xffffffffffffffff).to_bytes(8, byteorder='little')
        pdwBlock = [pdwBlockId, res4, pdwSize]

        # Build PDW file from header, padBlock, pdwBlock, and PDWs
        pdwFile = header + padding + pdwBlock
        pdwFile += [self.bin_pdw_builder(*p) for p in pdwList]
        # Convert arrays of data to a single byte-type variable
        pdwFile = b''.join(pdwFile)

        if self.errCheck:
            self.err_check()

        return pdwFile

    def csv_pdw_file_download(self, fileName, fields=['Operation', 'Time'],
                              data=[[1, 0], [2, 100e-6]]):
        """
        Builds a CSV PDW file, sends it into the UXG, and converts it to a binary PDW file.
        Args:
            fileName (str): Name of the csv file to be downloaded.
            fields (tuple(str)): Names of the fields contained in PDWs.
            data (tuple(tuple)): Tuple of tuples. The inner tuples each contain the values for the fields for a single PDW.
        """

        # Write header fields separated by commas and terminated with \n
        pdwCsv = ','.join(fields) + '\n'
        for row in data:
            # Write subsequent rows with data values separated by commas and terminated with \n
            # The .join() function requires a list of strings, so convert numbers in row to strings
            rowString = ','.join([f'{r}' for r in row]) + '\n'
            pdwCsv += rowString

        # Delete pdw csv file if already exists, continue script if it doesn't
        try:
            self.write('stream:state off')
            self.write(f'memory:delete "{fileName}.csv"')
            if self.errCheck:
                self.err_check()
        except error.SockInstError:
            pass
        self.binblockwrite(f'memory:data "{fileName}.csv", ', pdwCsv.encode('utf-8'))

        """Note: memory:import:stream imports/converts csv to pdw AND
        assigns the resulting pdw and waveform index files as the stream
        source. There is no need to send the stream:source:file or 
        stream:source:file:name commands because they are sent
        implicitly by memory:import:stream."""

        self.write(f'memory:import:stream "{fileName}.csv", "{fileName}"')
        self.query('*opc?')
        if self.errCheck:
            self.err_check()

    def csv_windex_file_download(self, windex):
        """
        Writes a waveform index file to be used by a PDW file to select
        waveforms.
        Args:
            windex (dict): {'fileName': '<fileName>', 'wfmNames': ['<name0>', '<name1>',... '<nameN>']}
        """

        windexCsv = 'Id,Filename\n'
        for i in range(len(windex['wfmNames'])):
            windexCsv += f'{i},{windex["wfmNames"][i]}\n'

        self.binblockwrite(f'memory:data "{windex["fileName"]}.csv", ', windexCsv.encode('utf-8'))

        """Note: memory:import:windex imports/converts csv to waveform
        index file AND assigns the resulting file as the waveform index
        manager. There is no need to send the stream:windex:select 
        command because it is sent implicitly by memory:import:windex."""
        self.write(f'memory:import:windex "{windex["fileName"]}.csv", "{windex["fileName"]}"')
        self.query('*opc?')
        if self.errCheck:
            self.err_check()

    def download_wfm(self, wfmData, wfmID='wfm'):
        """
        Defines and downloads a waveform into the waveform memory.
        Returns useful waveform identifier.
        Args:
            wfmData (NumPy array): Complex waveform values.
            wfmID (str): Waveform name.

        Returns:
            (str): Useful waveform identifier/name.
        """

        if wfmData.dtype != np.complex:
            raise TypeError('Invalid wfm type. IQ waveforms must be an array of complex values.')
        else:
            i = self.check_wfm(np.real(wfmData))
            q = self.check_wfm(np.imag(wfmData))

            wfm = self.iq_wfm_combiner(i, q)
        self.write('radio:arb:state off')

        self.arbState = self.query('radio:arb:state?').strip()
        self.binblockwrite(f'memory:data "WFM1:{wfmID}", ', wfm)

        return wfmID

    @staticmethod
    def iq_wfm_combiner(i, q):
        """
        Combines i and q wfms into a single interleaved wfm for download to generator.
        Args:
            i (NumPy array): Array of real waveform samples.
            q (NumPy array): Array of imaginary waveform samples.

        Returns:
            (NumPy array): Array of interleaved IQ values.
        """
        iq = np.empty(2 * len(i), dtype=np.uint16)
        iq[0::2] = i
        iq[1::2] = q
        return iq

    def check_wfm(self, wfm, bigEndian=True):
        """
        Checks minimum size and granularity and returns waveform with
        appropriate binary formatting. Note that sig gens expect big endian
        byte order.

        See pages 205-256 in Keysight X-Series Signal Generators Programming
        Guide (November 2014 Edition) for more info.
        Args:
            wfm (NumPy array): Unscaled/unformatted waveform data.
            bigEndian (bool): Determines whether waveform is big endian.

        Returns:
            (NumPy array): Waveform data that has been scaled and
                formatted appropriately for download to AWG
        """

        repeats = wraparound_calc(len(wfm), self.gran, self.minLen)
        wfm = np.tile(wfm, repeats)
        rl = len(wfm)

        if rl < self.minLen:
            raise error.VSGError(f'Waveform length: {rl}, must be at least {self.minLen}.')
        if rl % self.gran != 0:
            raise error.GranularityError(f'Waveform must have a granularity of {self.gran}.')

        if bigEndian:
            return np.array(self.binMult * wfm, dtype=np.uint16).byteswap()
        else:
            return np.array(self.binMult * wfm, dtype=np.uint16)

    def delete_wfm(self, wfmID):
        """
        Stops output and deletes specified waveform.
        Args:
            wfmID (str): Name of waveform to be deleted.
        """

        self.stop()
        self.write(f'mmemory:delete "{wfmID}", "WFM1:"')
        if self.errCheck:
            self.err_check()

    def clear_all_wfm(self):
        """Clears all waveform, pdw, and windex files. This function
        MUST be called prior to downloading waveforms and making
        changes to an existing pdw file."""

        self.write('stream:state off')
        self.write('radio:arb:state off')
        self.write('memory:delete:binary')
        self.write('mmemory:delete:wfm')
        self.query('*opc?')
        if self.errCheck:
            self.err_check()

    def play(self, wfmID='wfm'):
        """
        Selects waveform and activates arb mode, RF output, and modulation.
        Args:
            wfmID (str): Waveform identifier, used to select waveform to be played.
        """

        self.write(f'radio:arb:waveform "WFM1:{wfmID}"')
        self.write('radio:arb:state on')
        self.arbState = self.query('radio:arb:state?').strip()
        self.write('output on')
        self.rfState = self.query('output?').strip()
        self.write('output:modulation on')
        self.modState = self.query('output:modulation?').strip()
        if self.errCheck:
            self.err_check()

    def stop(self):
        """Dectivates RF output, modulation, and arb mode."""
        self.write('output off')
        self.rfState = self.query('output?').strip()
        self.write('output:modulation off')
        self.modState = self.query('output:modulation?').strip()
        self.write('radio:arb:state off')
        self.arbState = self.query('radio:arb:state?').strip()
        if self.errCheck:
            self.err_check()

    def stream_play(self, pdwID='pdw', wIndexID=None):
        """
        Assigns pdw/windex, activates RF output, modulation, and
        streaming mode, and triggers streaming output.
        Args:
            pdwID (str): Name of the PDW file to be loaded.
            wIndexID (str): Name of the waveform index file to be loaded.
                Default argument of None will load a waveform index file
                with the same name as the PDW file.
        """

        self.write('stream:source file')
        self.write(f'stream:source:file:name "{pdwID}"')

        # If wIndexID is unspecified, use the same name as the pdw file.
        if wIndexID is None:
            self.write(f'stream:windex:select "{pdwID}"')
        else:
            self.write(f'stream:windex:select "{wIndexID}"')

        # Activate streaming, and send trigger command.
        self.write('output:modulation on')
        self.modState = self.query('output:modulation?').strip()
        self.write('stream:state on')
        self.streamState = self.query('stream:state?').strip()
        self.write('stream:trigger:play:immediate')
        if self.errCheck:
            self.err_check()

    def stream_stop(self):
        """Deactivates RF output, modulation, and streaming mode."""
        self.write('output off')
        self.rfState = self.query('output?').strip()
        self.write('output:modulation off')
        self.modState = self.query('output:modulation?').strip()
        self.write('stream:state off')
        self.streamState = self.query('stream:state?').strip()
        if self.errCheck:
            self.err_check()
