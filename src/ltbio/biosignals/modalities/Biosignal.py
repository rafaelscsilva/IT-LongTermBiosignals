# -*- encoding: utf-8 -*-

# ===================================

# IT - LongTermBiosignals

# Package: biosignals
# Module: Biosignal
# Description: The base class holding all data related to a biosignal and its channels.

# Contributors: João Saraiva, Mariana Abreu
# Last Updated: 09/07/2022

# ===================================


from abc import ABC, abstractmethod, ABCMeta
from copy import deepcopy
from datetime import datetime, timedelta
from inspect import isclass
from math import ceil
from typing import Dict, Tuple, Collection, Set, ClassVar

import matplotlib.pyplot as plt
import numpy as np
from datetimerange import DateTimeRange
from dateutil.parser import parse as to_datetime, ParserError
from numpy import ndarray, array

from ltbio.biosignals.timeseries.Unit import Unitless
from ltbio.biosignals.sources.BiosignalSource import BiosignalSource
from ltbio.biosignals.timeseries.Event import Event
from .. import timeseries
from ltbio.clinical.conditions.MedicalCondition import MedicalCondition
#from ...processing.filters.Filter import Filter
from ltbio.clinical.BodyLocation import BodyLocation
from ltbio.clinical.Patient import Patient
from ltbio.processing.noises.Noise import Noise


class Biosignal(ABC):
    '''
    A Biosignal is a set of Timeseries, called channels, of samples measuring a biological variable.
    It may be associated with a source, a patient, and a body location. It can also have a name.
    It has an initial and final datetime. Its length is its number of channels.
    It can be resampled, filtered, and concatenated to other Biosignals.
    Amplitude and spectrum plots can be displayed and saved.
    '''

    __SERIALVERSION: int = 1

    def __init__(self, timeseries: Dict[str|BodyLocation, timeseries.Timeseries] | str | Tuple[datetime], source:BiosignalSource.__subclasses__()=None, patient:Patient=None, acquisition_location:BodyLocation=None, name:str=None):
        self.__name = name
        self.__source = source
        self.__patient = patient
        self.__acquisition_location = acquisition_location
        self.__associated_events = {}
        self.__added_noise = None

        # Handle timeseries
        if isinstance(timeseries, str): # this should be a filepath -> read samples from file
            if source is None:
                raise ValueError("To read a biosignal from a file, specify the biosignal source.")
            else:
                read_data = self.source._read(timeseries, type=type(self) )

                if isinstance(read_data, dict):  # Get Timeseries
                    self.__timeseries = read_data

                elif isinstance(read_data, tuple):  # Get Timeseries and location
                    self.__timeseries = read_data[0]
                    self.__acquisition_location = read_data[1]

                # Get Events, if any
                events = self.source._events(timeseries)
                if events is not None:
                    self.associate(events)


        if isinstance(timeseries, datetime): # this should be a time interval -> fetch from database
            pass # TODO

        if isinstance(timeseries, dict): # this should be the {chanel name: Timeseries} -> save samples directly
            self.__timeseries = timeseries
            # Check if Timeseries come with Events associated
            for ts in timeseries.values():
                for event in ts.events:
                    if event.name in self.__associated_events and self.__associated_events[event.name] != event:
                        raise AssertionError("There are different Events with the same name among the Timeseries given.")
                    else:
                        self.__associated_events[event.name] = event

        if self.__acquisition_location is not None:
            self.__acquisition_location = acquisition_location  # override with user input

    def __copy__(self):
        return type(self)({ts: self.__timeseries[ts].__copy__() for ts in self.__timeseries}, self.__source, self.__patient, self.__acquisition_location, str(self.__name))

    def _new(self, timeseries: Dict[str|BodyLocation, timeseries.Timeseries] | str | Tuple[datetime] = None, source:BiosignalSource.__subclasses__()=None, patient:Patient=None, acquisition_location:BodyLocation=None, name:str=None, events:Collection[Event]=None, added_noise=None):
        timeseries = {ts: self.__timeseries[ts].__copy__() for ts in self.__timeseries} if timeseries is None else timeseries  # copy
        source = self.__source if source is None else source  # no copy
        patient = self.__patient if patient is None else patient  # no copy
        acquisition_location = self.__acquisition_location if acquisition_location is None else acquisition_location  # no copy
        name = str(self.__name) if name is None else name  # copy

        new = type(self)(timeseries, source, patient, acquisition_location, name)

        # Associate events; no need to copy
        events = self.__associated_events if events is None else events
        events = events.values() if isinstance(events, dict) else events
        # Check if some event can be associated
        for event in events:
            try:
                new.associate(event)
            except ValueError:  # outside the domain of every channel
                pass  # no problem; the Event will not be associated

        # Associate added noise reference:
        if added_noise is not None:
            new._Biosignal__added_noise = added_noise

        return new

    def _apply_operation_and_new(self, operation,
                                 source:BiosignalSource.__subclasses__()=None, patient:Patient=None,
                                 acquisition_location:BodyLocation=None, name:str=None, events:Collection[Event]=None,
                                 **kwargs):
        new_channels = {}
        for channel_name in self.channel_names:
            new_channels[channel_name] = self.__timeseries[channel_name]._apply_operation_and_new(operation, **kwargs)
        return self._new(new_channels, source=source, patient=patient, acquisition_location=acquisition_location,
                         name=name, events=events)

    def _apply_operation_and_return(self, operation, **kwargs):
        pass  # TODO

    @property
    def __has_single_channel(self) -> bool:
        return len(self) == 1

    def _get_channel(self, channel_name:str|BodyLocation) -> timeseries.Timeseries:
        if channel_name in self.channel_names:
            return self.__timeseries[channel_name]
        else:
            raise AttributeError(f"No channel named '{channel_name}'.")

    def _get_single_channel(self) -> tuple[str|BodyLocation, timeseries.Timeseries]:
        """
        :return: channel_name, channel
        """
        if not self.__has_single_channel:
            raise AttributeError(f"This Biosignal does not have a single channel. It has multiple channels.")
        return tuple(self.__timeseries.items())[0]

    def get_event(self, name: str) -> Event:
        if name in self.__associated_events:
            return self.__associated_events[name]
        from_conditions = self.__get_events_from_medical_conditions()
        if name in from_conditions:
            return from_conditions[name]
        else:
            raise NameError(f"No Event named '{name}' associated to the Biosignal or its paitent's conditions.")

    @property
    def preview(self):
        """Returns 5 seconds of the middle of the signal."""
        domain = self.domain
        middle_of_domain:DateTimeRange = domain[len(domain)//2]
        middle = middle_of_domain.start_datetime + (middle_of_domain.timedelta / 2)
        try:
            return self[middle - timedelta(seconds=2) : middle + timedelta(seconds=3)]
        except IndexError:
            raise AssertionError(f"The middle segment of {self.name} from {self.patient_code} does not have at least 5 seconds to return a preview.")

    def __getitem__(self, item):
        '''The built-in slicing and indexing operations.'''

        if isinstance(item, datetime):
            if len(self) != 1:
                raise IndexError("This Biosignal has multiple channels. Index the channel before indexing the datetime.")
            return tuple(self.__timeseries.values())[0][item]

        if isinstance(item, (str, BodyLocation)):
            if item in self.channel_names:
                if len(self) == 1:
                    raise IndexError("This Biosignal only has 1 channel. Index only the datetimes.")
                ts = {item: self.__timeseries[item].__copy__(), }
                return self._new(timeseries=ts)

            elif item in self.__associated_events or item in self.__get_events_from_medical_conditions():
                if item in self.__associated_events:  # Internal own Events
                    event = self.__associated_events[item]
                else:  # Events associated to MedicalConditions
                    event = self.__get_events_from_medical_conditions()[item]

                if event.has_onset and event.has_offset:
                    return self[DateTimeRange(event.onset, event.offset)]
                elif event.has_onset:
                    return self[event.onset]
                elif event.has_offset:
                    return self[event.offset]

            else:
                try:
                    self.__timeseries[to_datetime(item)]
                except:
                    raise IndexError("Datetime in incorrect format or '{}' is not a channel nor an event of this Biosignal.".format(item))

        def __get_events_with_padding(event_name, padding_before=timedelta(seconds=0), padding_after=timedelta(seconds=0), exclude_event = False):
            # Get Event object
            if event_name in self.__associated_events:
                event = self.__associated_events[event_name]
            elif event_name in self.__get_events_from_medical_conditions():
                event = self.__get_events_from_medical_conditions()[event_name]
            else:
                raise IndexError(f"No Event named '{event_name}' associated to this Biosignal.")

            if isinstance(padding_before, datetime) and isinstance(padding_after, datetime) and exclude_event:
                if event.has_onset and event.has_offset:
                    return self[DateTimeRange(padding_before, event.onset)] >> self[DateTimeRange(event.offset + timedelta(seconds=1/self.sampling_frequency), padding_after)]  # FIXME: Sampling frequency might not be the same for all channels!
                else:
                    raise IndexError(f"Event {event_name} is a point in time, not an event with a duration.")

            # Convert specific datetimes to timedeltas; is this inneficient?
            if isinstance(padding_before, datetime):
                if event.has_onset:
                    padding_before = event.onset - padding_before
                elif event.has_offset:
                    padding_before = event.offset - padding_before
                if exclude_event:
                    padding_after = - event.duration
            if isinstance(padding_after, datetime):
                if event.has_offset:
                    padding_after = padding_after - event.offset
                elif event.has_onset:
                    padding_after = padding_after - event.onset
                if exclude_event:
                    padding_before = - event.duration

            # Index
            if event.has_onset and event.has_offset:
                return self[DateTimeRange(event.onset - padding_before, event.offset + padding_after)]
            elif event.has_onset:
                return self[DateTimeRange(event.onset - padding_before, event.onset + padding_after)]
            elif event.has_offset:
                return self[DateTimeRange(event.offset - padding_before, event.offset + padding_after)]

        if isinstance(item, slice):

            # Everything but event
            if isinstance(item.stop, str) and item.start is None and item.step is None:
                if not item.stop.startswith('-'):
                    raise ValueError(
                        "Indexing a Biosignal like x[:'event':] is equivalent to having its entire domain. Did you mean x[:'-event':]?")
                return __get_events_with_padding(item.stop[1:], padding_before=self.initial_datetime, padding_after=self.final_datetime,
                                                 exclude_event=True)

            # Everything before event
            if isinstance(item.stop, str) and item.start is None:
                event_name, exclude_event = item.stop, False
                if event_name.startswith('-'):
                    event_name, exclude_event = event_name[1:], True
                return __get_events_with_padding(event_name, padding_before=self.initial_datetime, exclude_event=exclude_event)

            # Everything after event
            if isinstance(item.start, str) and item.stop is None:
                event_name, exclude_event = item.start, False
                if event_name.startswith('-'):
                    event_name, exclude_event = event_name[1:], True
                return __get_events_with_padding(event_name, padding_after=self.final_datetime, exclude_event=exclude_event)

            # Event with padding
            if isinstance(item.start, (timedelta, int)) and isinstance(item.step, (timedelta, int)) and isinstance(item.stop, str):
                start = timedelta(seconds=item.start) if isinstance(item.start, int) else item.start  # shortcut for seconds
                step = timedelta(seconds=item.step) if isinstance(item.step, int) else item.step  # shortcut for seconds
                return __get_events_with_padding(item.stop, padding_before=start, padding_after=step)
            elif isinstance(item.start, (timedelta, int)) and isinstance(item.stop, str):
                start = timedelta(seconds=item.start) if isinstance(item.start, int) else item.start  # shortcut for seconds
                return __get_events_with_padding(item.stop, padding_before=start)
            elif isinstance(item.start, str) and isinstance(item.stop, (timedelta, int)):
                stop = timedelta(seconds=item.stop) if isinstance(item.stop, int) else item.stop  # shortcut for seconds
                return __get_events_with_padding(item.start, padding_after=stop)

            # Index by datetime
            if isinstance(item.start, datetime) and isinstance(item.stop, datetime) and item.stop < item.start:
                raise IndexError("Given final datetime comes before the given initial datetime.")

            if self.__has_single_channel:  # one channel
                channel_name = tuple(self.__timeseries.keys())[0]
                channel = self.__timeseries[channel_name]
                return self._new(timeseries={channel_name: channel[item]})

            else:  # multiple channels
                ts = {}
                events = set()
                for k in self.channel_names:
                    ts[k] = self.__timeseries[k][item]
                    # Events outside the new domain get discarded, hence collecting the ones that remained
                    events.update(set(self.__timeseries[k].events))
                new = self._new(timeseries=ts, events=events)
                return new

        if isinstance(item, DateTimeRange):  # Pass item directly to each channel
            ts = {}
            events = set()
            for k in self.channel_names:
                res = self.__timeseries[k][item]
                if res is not None:
                    ts[k] = res
                    # Events outside the new domain get discarded, hence collecting the ones that remained
                    events.update(set(self.__timeseries[k].events))

            if len(ts) == 0:
                raise IndexError(f"Event is outside every channel's domain.")

            new = self._new(timeseries=ts, events=events)


            try:  # to associate events, if they are inside the domain
                new.associate(events)
            except ValueError:
                pass


            return new


        if isinstance(item, tuple):
            if len(self) == 1:
                res = list()
                for k in item:
                    if isinstance(k, datetime):
                        res.append(self.__timeseries[k])
                    if isinstance(k, str):
                        try:
                            res.append(self.__timeseries[to_datetime(k)])
                        except ParserError:
                            raise IndexError("String datetimes must be in a correct format.")
                    else:
                        raise IndexError("Index types not supported. Give a tuple of datetimes (can be in string format).")
                return tuple(res)

            else:
                ts = {}
                events = set()
                for k in item:
                    if isinstance(k, datetime):
                        raise IndexError("This Biosignal has multiple channels. Index the channel before indexing the datetimes.")
                    if isinstance(k, str) and (k not in self.channel_names):
                        raise IndexError("'{}' is not a channel of this Biosignal.".format(k))
                    if not isinstance(k, str):
                        raise IndexError("Index types not supported. Give a tuple of channel names (in str).")
                    ts[k] = self.__timeseries[k]
                    events.update(set(self.__timeseries[k].events))
                new = self._new(timeseries=ts, events=events)
                return new

        raise IndexError("Index types not supported. Give a datetime (can be in string format), a slice or a tuple of those.")


    @property
    def channel_names(self) -> set[str | BodyLocation]:
        '''Returns a tuple with the labels associated to every channel.'''
        return set(self.__timeseries.keys())

    @property
    def name(self):
        '''Returns the associated name, or 'No Name' if none was provided.'''
        return self.__name if self.__name != None else "No Name"

    @name.setter
    def name(self, name:str):
        self.__name = name

    @property
    def patient_code(self):
        '''Returns the code of the associated Patient, or 'n.d.' if none was provided.'''
        return self.__patient.code if self.__patient != None else 'n.d.'

    @property
    def patient_conditions(self) -> Set[MedicalCondition]:
        '''Returns the set of medical conditions of the associated Patient, or None if no Patient was associated.'''
        return self.__patient.conditions if self.__patient != None else set()

    @property
    def acquisition_location(self):
        '''Returns the associated acquisition location, or None if none was provided.'''
        return self.__acquisition_location

    @property
    def source(self) -> BiosignalSource:
        '''Returns the BiosignalSource from where the data was read, or None if was not specified.'''
        return self.__source

    @property
    def type(self) -> ClassVar:
        '''Returns the biosignal modality class. E.g.: ECG, EMG, EDA, ...'''
        return type(self)

    @property
    def initial_datetime(self) -> datetime:
        '''Returns the initial datetime of the channel that starts the earliest.'''
        return min([ts.initial_datetime for ts in self.__timeseries.values()])

    @property
    def final_datetime(self) -> datetime:
        '''Returns the final datetime of the channel that ends the latest.'''
        return max([ts.final_datetime for ts in self.__timeseries.values()])

    @property
    def domain(self) -> Tuple[DateTimeRange]:
        if len(self) == 1:
            return tuple(self.__timeseries.values())[0].domain
        else:
            channels = tuple(self.__timeseries.values())
            cumulative_intersection:Tuple[DateTimeRange]
            for k in range(1, len(self)):
                if k == 1:
                    cumulative_intersection = channels[k].overlap(channels[k-1])
                else:
                    cumulative_intersection = channels[k].overlap(cumulative_intersection)
            return cumulative_intersection

    @property
    def subdomains(self) -> Tuple[DateTimeRange]:
        if len(self) == 1:
            return tuple(self.__timeseries.values())[0].subdomains
        else:
            raise NotImplementedError()

    def _vblock(self, i: int):
        """
        Returns a block of timelly allined segments, vertially alligned for all channels.
        Note: This assumes all channels are segmented in the same way, i.e., have exactly the same set of subdomains.
        :param i: The block index
        :return: ndarray of vertical stacked segmetns
        """
        N = self._n_segments
        if isinstance(N, int):
            if i < N:
                return np.vstack([channel[i] for channel in self.__timeseries.values()])
            else:
                IndexError(f"This Biosignal as only {N} blocks.")
        else:
            raise AssertionError("Not all channels are segmented in the same way, hence blocks cannot be created.")

    def _block_subdomain(self, i: int) -> DateTimeRange:
        if len(self) == 1:
            return tuple(self.__timeseries.values())[0]._block_subdomain(i)
        else:
            raise NotImplementedError()

    @property
    def _n_segments(self) -> int | dict:
        """
        Returns the number of segments of each Timeseries.
        :rtype: dict, with the number of segments labelled by channel name; or int if they are all the same
        """
        n_segments = {}
        last_n = None
        all_equal = True
        for channel_name, channel in self.__timeseries.items():
            x = channel.n_segments
            last_n = x
            n_segments[channel_name] = x
            if last_n is not None and last_n != x:
                all_equal = False
        if all_equal:
            return last_n
        else:
            return n_segments

    @property
    def duration(self):
        common_duration = tuple(self.__timeseries.values())[0].duration
        for _, channel in self:
            if channel.duration != common_duration:
                raise AssertionError("Not all channels have the same duration.")
        return common_duration

    def __get_events_from_medical_conditions(self):
        res = {}
        for condition in self.patient_conditions:
            res.update(condition._get_events())
        return res

    @property
    def events(self):
        '''Tuple of associated Events, ordered by datetime.'''
        return tuple(sorted(list(self.__associated_events.values()) + list(self.__get_events_from_medical_conditions().values())))

    @property
    def sampling_frequency(self) -> float:
        '''Returns the sampling frequency of every channel (if equal), or raises an error if they are not equal.'''
        if len(self) == 1:
            return tuple(self.__timeseries.values())[0].sampling_frequency
        else:
            common_sf = None
            for _, channel in self:
                if common_sf is None:
                    common_sf = channel.sampling_frequency
                elif channel.sampling_frequency != common_sf:
                    raise AttributeError("Biosignal contains 2+ channels, all not necessarly with the same sampling frequency.")
            return common_sf

    @property
    def added_noise(self):
        '''Returns a reference to the noisy component, if the Biosignal was created with added noise; else the property does not exist.'''
        if self.__added_noise is not None:
            return self.__added_noise
        else:
            raise AttributeError("No noise was added to this Biosignal.")

    def __len__(self):
        '''Returns the number of channels.'''
        return len(self.__timeseries)

    def __repr__(self):
        '''Returns a textual description of the Biosignal.'''
        res = "Name: {}\nType: {}\nLocation: {}\nNumber of Channels: {}\nChannels: {}\nUseful Duration: {}\nSource: {}\n".format(
            self.name,
            self.type.__name__,
            self.acquisition_location,
            len(self),
            ''.join([(x + ', ') for x in self.channel_names]),
            self.duration,
            self.source.__str__(None) if isinstance(self.source, ABCMeta) else str(self.source))

        if len(self.__associated_events) != 0:
            res += "Events:\n"
            for event in sorted(self.__associated_events.values()):
                res += '- ' + str(event) + '\n'
        events_from_medical_conditions = dict(sorted(self.__get_events_from_medical_conditions().items(), key=lambda item: item[1]))
        if len(events_from_medical_conditions) != 0:
            res += "Events associated to Medical Conditions:\n"
            for key, event in events_from_medical_conditions.items():
                res += f"- {key}:\n{event}\n"
        return res

    def _to_dict(self) -> Dict[str|BodyLocation, timeseries.Timeseries]:
        return deepcopy(self.__timeseries)

    def _to_array(self) -> ndarray:
        """
        Converts Biosignal to a NumPy ndarray.
        :return: A C x M x N array, where C is the number of channels, M the number of segments of each, and N their length.
        :rtype: list[numpy.ndarray]
        """
        x = [channel._to_array() for channel in self.__timeseries.values()]
        return np.stack(x)

    def __iter__(self):
        return self.__timeseries.items().__iter__()

    def __contains__(self, item):
        if isinstance(item, str):
            if item in self.__timeseries.keys():  # if channel exists
                return True
            if item in self.__associated_events:  # if Event occurs
                return True
            events_from_consitions = self.__get_events_from_medical_conditions()
            for label, event in events_from_consitions:
                if item == label and event.domain in self:
                    return True
            return False
        elif isinstance(item, (datetime, DateTimeRange)):
            for _, channel in self:
                if item in channel:  # if at least one channel defines this point in time
                    return True
            return False
        else:
            raise TypeError(f'Cannot apply this operation with {type(item)}.')

    def __mul__(self, other):
        if isinstance(other, (float, int)):
            suffix = f' (dilated up by {str(other)})' if other > 1 else f' (compressed up by {str(other)})'
            return self._apply_operation_and_new(lambda x: x*other, name=self.name + suffix)

    def __sub__(self, other):
        return self + (other * -1)

    def __neg__(self):
        return self * -1

    def __add__(self, other):
        """
        If a float or int:
            Add constant to every channel. Translation of the signal.
        If Biosignal:
            Adds both sample-by-sample, if they have the same domain.
        Notes:
            - If the two Biosignals have two distinct acquisition locations, they will be lost.
            - If the two Biosignals have two distinct sources, they will be lost.
            - If the two Biosignals have the distict patients, they will be lost.
        Raises:
            - TypeError if Biosignals are not of the same type.
            - ArithmeticError if Biosignals do not have the same domain.
        """

        if isinstance(other, (float, int)):
            return self._apply_operation_and_new(lambda x: x+other, name=self.name + f' (shifted up by) {str(other)}')

        if isinstance(other, Biosignal):
            # Check errors
            if self.type != other.type:
                while True:
                    answer = input(f"Trying to add an {self.type.__name__} with an {other.type.__name__}. Do you mean to add templeates of the second as noise? (y/n)")
                    if answer.lower() in ('y', 'n'):
                        if answer.lower() == 'y':
                            return Biosignal.withAdditiveNoise(self, other)
                        else:
                            raise TypeError("Cannot add a {0} to a {1} if not as noise.".format(other.type.__name__, self.type.__name__))
            if self.channel_names != other.channel_names:
                raise ArithmeticError("Biosignals to add must have the same channel names.")
            if self.domain != other.domain:
                raise ArithmeticError("Biosignals to add must have the same domains.")

            # Prepare common metadata
            name = f"{self.name} + {other.name}"
            acquisition_location = self.acquisition_location if self.acquisition_location == other.acquisition_location else None
            patient = self.__patient if self.patient_code == other.patient_code else None
            if isclass(self.source) and isclass(other.source):  # Un-instatiated sources
                if self.source == other.source:
                    source = self.__source
                else:
                    source = None
            else:
                if type(self.source) == type(other.source) and self.source == other.source:
                    source = self.__source
                else:
                    source = None

            # Perform addition
            res_timeseries = {}
            for channel_name in self.channel_names:
                res_timeseries[channel_name] = self._to_dict()[channel_name] + other._to_dict()[channel_name]

            # Union of Events
            events = set(self.events).union(set(other.events))

            return self._new(timeseries=res_timeseries, source=source, patient=patient, acquisition_location=acquisition_location, name=name, events=events)

        raise TypeError(f"Addition operation not valid with Biosignal and object of type {type(other)}.")

    def __and__(self, other):
        """
        Joins the channels of two Biosignals of the same type, if they do not have the same set of channel names.
        Notes:
            - If the two Biosignals have two distinct acquisition locations, they will be lost.
            - If the two Biosignals have two distinct sources, they will be lost.
            - If the two Biosignals have the distict patients, they will be lost.
        Raises:
            - TypeError if Biosignals are not of the same type.
            - ArithmeticError if both Biosignals have any channel name in common.
        """

        # Check errors
        if not isinstance(other, Biosignal):
            raise TypeError(f"Operation join channels is not valid with object of type {type(other)}.")
        if self.type != other.type:
            raise TypeError("Cannot join a {0} to a {1}".format(other.type.__name__, self.type.__name__))
        if len(self.channel_names.intersection(other.channel_names)) != 0:
            raise ArithmeticError("Channels to join cannot have the same names.")

        # Prepare common metadata
        name = f"{self.name} and {other.name}"
        acquisition_location = self.acquisition_location if self.acquisition_location == other.acquisition_location else None
        patient = self.__patient if self.patient_code == other.patient_code else None
        source = type(self.source) if ((isinstance(self.source, ABCMeta) and isinstance(other.source, ABCMeta)
                                       and self.source == other.source) or
                                       (type(self.source) == type(other.source))
                                       ) else None

        # Join channels
        res_timeseries = {}
        res_timeseries.update(self._to_dict())
        res_timeseries.update(other._to_dict())

        # Union of Events
        events = set(self.events).union(set(other.events))

        return self._new(timeseries=res_timeseries, source=source, patient=patient, acquisition_location=acquisition_location, name=name, events=events)

    def __rshift__(self, other):
        """
        Temporally concatenates two Biosignal, if they have the same set of channel names.
        Notes:
            - If the two Biosignals have two distinct acquisition locations, they will be lost.
            - If the two Biosignals have two distinct sources, they will be lost.
            - If the two Biosignals have the distict patients, they will be lost.
        Raises:
            - TypeError if Biosignals are not of the same type.
            - ArithmeticError if both Biosignals do not have the same channel names.
            - ArithmeticError if the second comes before the first.
        """

        # Check errors
        if not isinstance(other, Biosignal):
            raise TypeError(f"Operation join channels is not valid with object of type {type(other)}.")
        if self.type != other.type:
            raise TypeError("Cannot join a {0} to a {1}".format(other.type.__name__, self.type.__name__))
        if self.channel_names != other.channel_names:
            raise ArithmeticError("Biosignals to concatenate must have the same channel names.")
        if other.initial_datetime < self.final_datetime:
            raise ArithmeticError("The second Biosignal comes before (in time) the first Biosignal.")

        # Prepare common metadata
        name = f"{self.name} >> {other.name}"
        acquisition_location = self.acquisition_location if self.acquisition_location == other.acquisition_location else None
        patient = self.__patient if self.patient_code == other.patient_code else None
        source = type(self.source) if ((isinstance(self.source, ABCMeta) and isinstance(other.source, ABCMeta)
                                        and self.source == other.source) or
                                       (type(self.source) == type(other.source))
                                       ) else None

        # Perform concatenation
        res_timeseries = {}
        for channel_name in self.channel_names:
            res_timeseries[channel_name] = self._to_dict()[channel_name] >> other._to_dict()[channel_name]

        # Union of Events
        events = set(self.__associated_events).union(set(other._Biosignal__associated_events))

        return self._new(timeseries=res_timeseries, source=source, patient=patient, acquisition_location=acquisition_location, name=name,
                         events=events)


    def set_channel_name(self, current:str|BodyLocation, new:str|BodyLocation):
        if current in self.__timeseries.keys():
            self.__timeseries[new] = self.__timeseries[current]
            del self.__timeseries[current]
        else:
            raise AttributeError(f"Channel named '{current}' does not exist.")

    def set_event_name(self, current:str, new:str):
        if current in self.__associated_events.keys():
            event = self.__associated_events[current]
            self.__associated_events[new] = Event(new, event._Event__onset, event._Event__offset)
            del self.__associated_events[current]
        else:
            raise AttributeError(f"Event named '{current}' is not associated.")

    def delete_events(self):
        for _, channel in self:
            channel.delete_events()
        self.__associated_events = {}

    def filter(self, filter_design) -> int:
        '''
        Filters every channel with to the given filter_design.

        @param filter_design: A Filter object specifying the designed filter to be applied.
        @return: 0 if the filtering is applied successfully.
        @rtype: int
        '''
        for channel in self.__timeseries.values():
            channel._accept_filtering(filter_design)
        return 0

    def undo_filters(self):
        '''
        Restores the raw samples of every channel, eliminating the action of any applied filter.
        '''
        for channel in self.__timeseries.values():
            channel._undo_filters()

    def resample(self, frequency:float):
        '''
        Resamples every channel to the new sampling frequency given, using Fourier method.
        @param frequency: New sampling frequency (in Hertz).
        '''
        for channel in self.__timeseries.values():
            channel._resample(frequency)

    def __draw_plot(self, timeseries_plotting_method, title, xlabel, ylabel, grid_on:bool, show:bool=True, save_to:str=None):
        '''
        Draws a base plot to display every channel in a subplot. It is independent of the content that is plotted.

        @param timeseries_plotting_method: The method to be called in Timeseries, that defines what content to plot.
        @param title: What the content is about. The Biosignal's name and patient code will be added.
        @param xlabel: Label for the horizontal axis.
        @param ylabel: Label for the vertical axis.
        @param grid_on: True if grid in to be drawn or not; False otherwise.
        @param show: True if plot is to be immediately displayed; False otherwise.
        @param save_to: A path to save the plot as an image file; If none is provided, it is not saved.
        @return:
        '''
        fig = plt.figure(figsize=(13, 2.5*len(self)))

        all_events = self.events
        all_onsets = [e.onset for e in all_events if e.has_onset]
        all_offsets = [e.offset for e in all_events if e.has_offset]
        all_vlines = all_onsets+all_offsets

        for i, channel_name in zip(range(len(self)), self.channel_names):
            channel = self.__timeseries[channel_name]
            ax = plt.subplot(len(self), 1, i+1, title=channel_name)
            ax.title.set_size(10)
            ax.margins(x=0)
            ax.set_xlabel(xlabel, fontsize=8, rotation=0, loc="right")
            ax.set_ylabel(ylabel, fontsize=8, rotation=90, loc="top")
            plt.xticks(fontsize=9)
            plt.yticks(fontsize=9)
            if grid_on:
                ax.grid()
            timeseries_plotting_method(self=channel)

            _vlines = [int((t - channel.initial_datetime).total_seconds() * channel.sampling_frequency) for t in all_vlines if t in channel]
            plt.vlines(_vlines, ymin=channel.min(), ymax=channel.max(), colors='red')

        fig.suptitle((title + ' ' if title is not None else '') + self.name + ' from patient ' + str(self.patient_code), fontsize=11)
        fig.tight_layout()
        if save_to is not None:
            fig.savefig(save_to)
        plt.show() if show else plt.close()

        #return fig

    def plot_spectrum(self, show:bool=True, save_to:str=None):
        '''
        Plots the Bode plot of every channel.
        @param show: True if plot is to be immediately displayed; False otherwise.
        @param save_to: A path to save the plot as an image file; If none is provided, it is not saved.
        '''
        self.__draw_plot(timeseries.Timeseries._plot_spectrum, 'Power Spectrum of', 'Frequency (Hz)', 'Power (dB)', True, show, save_to)

    def plot(self, show:bool=True, save_to:str=None):
        '''
        Plots the amplitude in time of every channel.
        @param show: True if plot is to be immediately displayed; False otherwise.
        @param save_to: A path to save the plot as an image file; If none is provided, it is not saved.
        '''
        return self.__draw_plot(timeseries.Timeseries._plot, None, 'Time', 'Amplitude (n.d.)', False, show, save_to)

    @abstractmethod
    def plot_summary(self, show:bool=True, save_to:str=None):
        '''
        Plots a summary of relevant aspects of common analysis of the Biosignal.
        '''
        pass  # Implemented in each type

    def apply_operation(self, operation, **kwargs):
        for channel in self.__timeseries.values():
            channel._apply_operation(operation, **kwargs)

    def invert(self, channel_label:str=None):
        inversion = lambda x: -1*x
        if channel_label is None:  # apply to all channels
            self.apply_operation(inversion)
        else:  # apply only to one channel
            self.__timeseries[channel_label]._apply_operation(inversion)

    def associate(self, events: Event | Collection[Event] | Dict[str, Event]):
        '''
        Associates an Event to all Timeseries.
        Events have names that serve as keys. If keys are given,
        i.e. if 'events' is a dict, then the Event names are overridden.
        @param events: One or multiple Event objects.
        @rtype: None
        '''

        def __add_event(event: Event):
            n_channels_associated = 0
            for _, channel in self:
                try:
                    channel.associate(event)
                    n_channels_associated += 1
                except ValueError:
                    pass
            if n_channels_associated > 0:  # If at least one association was possible
                self.__associated_events[event.name] = event
            else:
                raise ValueError(f"Event '{event.name}' is outside of every channel's domain.")

        if isinstance(events, Event):
            __add_event(events)
        elif isinstance(events, dict):
            for event_key in events:
                event = events[event_key]
                __add_event(Event(event_key, event._Event__onset, event._Event__offset))  # rename with given key
        else:
            for event in events:
                __add_event(event)

    def disassociate(self, event_name:str):
        '''
        Disassociates an Event from all Timeseries.
        @param event_name: The name of the Event to be removed.
        @rtype: None
        '''
        if event_name in self.__associated_events:
            for _, channel in self:
                try:
                    channel.disassociate(event_name)
                except NameError:
                    pass
            del self.__associated_events[event_name]
        else:
            raise NameError(f"There's no Event '{event_name}' associated to this Biosignal.")

    @classmethod
    def withAdditiveNoise(cls, original, noise, name:str = None):
        """
        Creates a new Biosignal from 'original' with added 'noise'.

        :param original: (Biosignal) The original Biosignal to be contaminated with noise.
        :param noise: (Noise | Timeseries | Biosignal) The noise to add to the original Biosignal.
        :param name: (str) The name to associate to the resulting Biosignal.

        When 'noise' is a Noise:
            - A trench of noise, with the duration of the channel, will be generated to be added to each channel.
            - 'noise' should be configured with the same sampling frequency has the channels.

        When 'noise' is a Biosignal:
            When it has the same set of channels as 'original', sampled at the same frequency:
                - Each noisy channel will be added to the corresponding channel of 'original', in a template-wise manner.
            When it has a unique channel:
                - That noisy channel will be added to every channel of 'original', in a template-wise manner.
                - That noisy channel should have the same sampling frequency has every channel of 'original'.
            - If 'noise' has multiple segments, they are concatenated to make a hyper-template.
            - Exception: in the case where both Timeseries having the same domain, the noisy samples will be added in a
                segment-wise manner.

        When 'noise' is a Timeseries sampled at the same frequency of 'original':
            - Its samples will be added to every channel of 'original', in a template-wise manner.
            - If 'noise' has multiple segments, they are concatenated to make a hyper-template.
            - Exception: in the case where both Timeseries having the same domain, the noisy samples will be added in a
                segment-wise manner.
            - 'noise' should have been sampled at the same frequency as 'original'.

        What is "template-wise manner"?
            - If the template segment is longer than any original segment, the template segment will be trimmed accordingly.
            - If the template segment is shorter than any original segment, the template will repeated in time.
            - If the two segments are of equal length, they are added as they are.

        :return: A Biosignal with the same properties as the 'original', but with noise added to the samples of every channel.
        :rtype: Biosignal subclass
        """

        if not isinstance(original, Biosignal):
            raise TypeError(f"Parameter 'original' must be of type Biosignal; but {type(original)} was given.")

        if not isinstance(noise, (Noise, timeseries.Timeseries, Biosignal)):
            raise TypeError(f"Parameter 'noise' must be of types Noise, Timeseries or Biosignal; but {type(noise)} was given.")

        if name is not None and not isinstance(name, str):
            raise TypeError(
                f"Parameter 'name' must be of type str; but {type(name)} was given.")

        def __add_template_noise(samples: ndarray, template: ndarray):
            # Case A
            if len(samples) < len(template):
                _template = template[:len(samples)]  # cut where it is enough
                return samples + _template  # add values
            # Case B
            elif len(samples) > len(template):
                _template = np.tile(template, ceil(len(samples)/len(template)))  # repeat full-pattern
                _template = _template[:len(samples)]  # cut where it is enough
                return samples + _template  # add values
            # Case C
            else:  # equal lengths
                return samples + template  # add values

        def __noisy_timeseries(original:timeseries.Timeseries, noise:timeseries.Timeseries) -> timeseries.Timeseries:
            # Case 1: Segment-wise
            if original.domain == noise.domain:
                template = [noise.samples, ] if noise.is_contiguous else noise.samples
                return original._apply_operation_and_new(__add_template_noise, template=template,
                                                         iterate_over_each_segment_key='template')
            # Case 2: Template-wise
            elif noise.is_contiguous:
                template = noise.samples
                return original._apply_operation_and_new(__add_template_noise, template=template)
            # Case 3: Template-wise, with hyper-template
            else:
                template = np.concatenate(noise.samples)  # concatenate as a hyper-template
                return original._apply_operation_and_new(__add_template_noise, template=template)

        noisy_channels = {}

        # Case Noise
        if isinstance(noise, Noise):
            for channel_name in original.channel_names:
                channel = original._get_channel(channel_name)
                if channel.sampling_frequency == noise.sampling_frequency:
                    template = noise[channel.duration]
                    noisy_channels[channel_name] = channel._apply_operation_and_new(__add_template_noise, template=template)
                else:
                    raise AssertionError(
                        f"Noise does not have the same sampling frequency as channel '{channel_name}' of 'original'."
                        f"Suggestion: Resample one of them first.")

        # Case Timeseries
        elif isinstance(noise, timeseries.Timeseries):
            for channel_name in original.channel_names:
                channel = original._get_channel(channel_name)
                if channel.units != noise.units and channel.units != None and channel.units != Unitless and noise.units != None and noise.units != Unitless:
                    raise AssertionError(
                        f"Noise does not have the same units as channel '{channel_name}' of 'original'."
                        f"Suggestion: If possible, convert one of them first or drop units.")
                if channel.sampling_frequency == noise.sampling_frequency:
                    noisy_channel = __noisy_timeseries(channel, noise)
                    noisy_channels[channel_name] = noisy_channel
                else:
                    raise AssertionError(
                        f"Noise does not have the same sampling frequency as channel '{channel_name}' of 'original'."
                        f"Suggestion: Resample one of them first.")


        elif isinstance(noise, Biosignal):
            # Case Biosignal channel-wise
            if original.channel_names == noise.channel_names:
                for channel_name in original.channel_names:
                    original_channel = original._get_channel(channel_name)
                    noise_channel = noise._get_channel(channel_name)
                    if original_channel.units != noise_channel.units and original_channel.units != None and original_channel.units != Unitless and noise_channel.units != None and noise_channel.units != Unitless:
                        raise AssertionError(
                            f"Noise does not have the same units as channel '{channel_name}' of 'original'."
                            f"Suggestion: If possible, convert one of them first or drop units.")
                    if original_channel.sampling_frequency == noise_channel.sampling_frequency:
                        noisy_channel = __noisy_timeseries(original_channel, noise_channel)
                        noisy_channels[channel_name] = noisy_channel
                    else:
                        raise AssertionError(f"Channels '{channel_name}' do not have the same sampling frequency in 'original' and 'noise'."
                                             f"Suggestion: Resample one of them first.")

            # Case Biosignal unique channel
            elif len(noise) == 1:
                _, x = tuple(iter(noise))[0]
                for channel_name in original.channel_names:
                    channel = original._get_channel(channel_name)
                    if channel.units != x.units and channel.units != None and channel.units != Unitless and x.units != None and x.units != Unitless:
                        raise AssertionError(
                            f"Noise does not have the same units as channel '{channel_name}' of 'original'."
                            f"Suggestion: If possible, convert one of them first or drop units.")
                    if channel.sampling_frequency == x.sampling_frequency:
                        noisy_channel = __noisy_timeseries(channel, x)
                        noisy_channels[channel_name] = noisy_channel
                    else:
                        raise AssertionError(f"Noise does not have the same sampling frequency as channel '{channel_name}' of 'original'."
                                             f"Suggestion: Resample one of them first.")

            else:
                raise ArithmeticError("Noise should have 1 channel only (to be added to every channel of 'original') "
                                      "or the same channels as 'original' (for each to be added to the corresponding channel of 'original'.")

        events = set.union(set(original.events), set(noise.events)) if isinstance(noise, (Biosignal, timeseries.Timeseries)) else None

        return original._new(timeseries = noisy_channels, name = name if name is not None else 'Noisy ' + original.name,
                             events = events, added_noise=noise)

    def restructure_domain(self, time_intervals:tuple[DateTimeRange]):
        domain = self.domain
        if len(domain) >= len(time_intervals):
            for _, channel in self:
                # 1. Concatenate segments
                channel._concatenate_segments()
                # 2. Partition according to new domain
                channel._partition(time_intervals)
        else:
            NotImplementedError("Not yet implemented.")

    def tag(self, tags: str | tuple[str]):
        """
        Mark all channels with a tag. Useful to mark machine learning targets.
        :param tags: The label or labels to tag the channels.
        :return: None
        """
        if isinstance(tags, str):
            for _, channel in self:
                channel.tag(tags)
        elif isinstance(tags, tuple) and all(isinstance(x, str) for x in tags):
            for x in tags:
                for _, channel in self:
                    channel.tag(x)
        else:
            raise TypeError("Give one or multiple string labels to tag the channels.")

    @classmethod
    def fromNoise(cls,
                  noises: Noise | Dict[str|BodyLocation, Noise],
                  time_intervals: DateTimeRange | tuple[DateTimeRange],
                  name: str = None):
        """
        Creates a type of Biosignal from a noise source.

        :param noises:
            - If a Noise object is given, the Biosignal will have 1 channel for the specified time interval.
            - If a dictionary of Noise objects is given, the Biosignal will have multiple channels, with different
            generated samples, for the specified time interval, named after the dictionary keys.

        :param time_interval: Interval [x, y[ where x will be the initial date and time of every channel, and y will be
        the final date and time of every channel; on a union of intervals, in case a tuple is given.

        :param name: The name to be associated to the Biosignal. Optional.

        :return: Biosignal subclass
        """

        if not isinstance(time_intervals, DateTimeRange) and isinstance(time_intervals, tuple) and \
                not all([isinstance(x, DateTimeRange) for x in time_intervals]):
            raise TypeError(f"Parameter 'time_interval' should be of type DateTimeRange or a tuple of them.")

        if isinstance(time_intervals, tuple) and len(time_intervals) == 1:
            time_intervals = time_intervals[0]

        channels = {}

        if isinstance(noises, Noise):
            if isinstance(time_intervals, DateTimeRange):
                samples = noises[time_intervals.timedelta]
                channels[noises.name] = timeseries.Timeseries(samples, time_intervals.start_datetime, noises.sampling_frequency,
                                                    units=Unitless(), name=noises.name)
            else:
                segments = {x.start_datetime: noises[x.timedelta] for x in time_intervals}
                channels[noises.name] = timeseries.Timeseries.withDiscontiguousSegments(segments, noises.sampling_frequency,
                                                   units=Unitless(), name=noises.name)

        elif isinstance(noises, dict):
            if isinstance(time_intervals, DateTimeRange):
                for channel_name, noise in noises.items():
                    samples = noise[time_intervals.timedelta]
                    channels[channel_name] = timeseries.Timeseries(samples, time_intervals.start_datetime, noise.sampling_frequency,
                                                        units=Unitless(), name=noise.name + f" : {channel_name}")
            else:
                for channel_name, noise in noises.items():
                    segments = {x.start_datetime: noise[x.timedelta] for x in time_intervals}
                    channels[channel_name] = timeseries.Timeseries.withDiscontiguousSegments(segments, noise.sampling_frequency,
                                                        units=Unitless(), name=noise.name + f" : {channel_name}")

        return cls(channels, name=name)

    # ===================================
    # SERIALIZATION

    def __getstate__(self):
        """
        1: __name (str)
        2: __source (BiosignalSource subclass (instantiated or not))
        3: __patient (Patient)
        4: __acquisition_location (BodyLocation)
        5: __associated_events (tuple)
        6: __timeseries (dict)
        """
        return (self.__SERIALVERSION, self.__name, self.__source, self.__patient, self.__acquisition_location,
                tuple(self.__associated_events.values()), self.__timeseries)

    def __setstate__(self, state):
        if state[0] == 1:
            self.__name, self.__source, self.__patient, self.__acquisition_location = state[1:5]
            self.__timeseries = state[6]
            self.__associated_events = {}
            self.associate(state[5])
        else:
            raise IOError(
                f'Version of {self.__class__.__name__} object not supported. Serialized version: {state[0]};'
                f'Supported versions: 1.')

    EXTENSION = '.biosignal'

    def save(self, save_to:str):
        # Check extension
        if not save_to.endswith(Biosignal.EXTENSION):
            save_to += Biosignal.EXTENSION

        # Write
        from bz2 import BZ2File
        from _pickle import dump
        with BZ2File(save_to, 'w') as f:
            dump(self, f)

    @classmethod
    def load(cls, filepath:str):
        # Check extension
        if not filepath.endswith(Biosignal.EXTENSION):
            raise IOError("Only .biosignal files are allowed.")

        # Read
        from bz2 import BZ2File
        from _pickle import load
        data = BZ2File(filepath, 'rb')
        return load(data)


class DerivedBiosignal(Biosignal):
    """
    A DerivedBiosignal is a set of Timeseries of some extracted feature from an original Biosignal.
    It is such a feature that it is useful to manipulate it as any other Biosignal.
    """

    def __init__(self, timeseries, source = None, patient = None, acquisition_location = None, name = None, original: Biosignal = None):
        if original is not None:
            super().__init__(timeseries, original.source, original._Biosignal__patient, original.acquisition_location, original.name)
        else:
            super().__init__(timeseries, source, patient, acquisition_location, name)

        self.original = original  # Save reference
