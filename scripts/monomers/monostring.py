# (c) 2020 by Authors
# This file is a part of centroFlye program.
# Released under the BSD license (see LICENSE file)

from enum import Enum
from itertools import count
import logging

import numpy as np

from utils.bio import RC, calc_identity
from utils.kmers import get_kmer_index_seq
from utils.various import fst_iterable

logger = logging.getLogger("centroFlye.monomers.monostring")


class Strand(Enum):
    FORWARD = '+'
    REVERSE = '-'

    @staticmethod
    def switch(strand):
        if strand == Strand.FORWARD:
            return Strand.REVERSE
        else:
            assert strand == Strand.REVERSE
            return Strand.FORWARD


class Reliability(Enum):
    RELIABLE = '+'
    UNRELIABLE = '?'


class MonoInstance:
    def __init__(self, monomer, sec_monomer, strand, sec_strand,
                 seq_id, nucl_segment,
                 st, en, seq_len,
                 reliability,
                 identity, sec_identity):
        assert en - st == len(nucl_segment)
        self.monomer = monomer
        self.sec_monomer = sec_monomer
        self.strand = strand
        self.sec_strand = sec_strand
        self.seq_id = seq_id
        self.nucl_segment = nucl_segment
        self.st = st
        self.en = en
        self.seq_len = seq_len
        self.reliability = reliability
        self.identity = identity
        self.sec_identity = sec_identity

    def get_monoindex(self):
        return self.monomer.mono_index

    def get_secmonoindex(self):
        return self.sec_monomer.mono_index

    def get_ref_seq(self):
        return self.monomer.seq

    def get_monoid(self):
        return self.monomer.monomer_id

    def get_secmonoid(self):
        return self.sec_monomer.monomer_id

    def is_lowercase(self):
        return self.strand == Strand.REVERSE

    def is_reliable(self):
        return self.reliability is Reliability.RELIABLE

    def reverse(self):
        self.nucl_segment = RC(self.nucl_segment)
        self.strand = Strand.switch(self.strand)
        # [st; en)
        self.st, self.en = self.seq_len - self.en, self.seq_len - self.st


class MonoString:
    # monostring is stored as a tuple because
    # |monomer_db| often exceeds |ascii|
    gap_symb = '?'
    reverse_symb = "'"
    none_monomer = 'None'

    def __init__(self, seq_id, monoinstances, raw_monostring, nucl_sequence,
                 monomer_db, is_reversed):
        self.seq_id = seq_id
        self.monoinstances = monoinstances
        self.raw_monostring = raw_monostring
        self.nucl_sequence = nucl_sequence
        self.monomer_db = monomer_db
        self.is_reversed = is_reversed
        self.corrections = {}  # dict pos (int) -> mono_index (int)
        assert_monostring_validity(self)

    @classmethod
    def from_sd_record(cls, seq_id, monomer_db, sd_record, nucl_sequence,
                       min_ident_diff=0.0, max_ident_for_diff=1):
        def get_monoinstances(sd_record):
            def id2index_strand(monomer_id, monomer_db=monomer_db):
                if monomer_id == cls.none_monomer:
                    index = None
                    strand = Strand.FORWARD
                    return index, strand

                if monomer_id[-1] == cls.reverse_symb:
                    monomer_id = monomer_id[:-1]
                    strand = Strand.REVERSE
                else:
                    strand = Strand.FORWARD
                index = monomer_db.id2index[monomer_id]
                return index, strand

            def get_reliablities(sd_record, identities, sec_identities):
                reliabilities = []
                for raw_rel, ident, sec_ident in zip(sd_record.reliability,
                                                     identities,
                                                     sec_identities):
                    reliability = Reliability(raw_rel)
                    # reliability below is currently disabled
                    if abs(ident - sec_ident) < min_ident_diff \
                            and sec_ident > max_ident_for_diff:
                        reliability = Reliability.UNRELIABLE
                    reliabilities.append(reliability)
                return reliabilities

            starts = sd_record.s_st.to_list()
            ends = [en + 1 for en in sd_record.s_en]

            ids = sd_record.monomer.to_list()
            indexes_strands = map(id2index_strand, ids)
            indexes, strands = zip(*indexes_strands)

            sec_ids = sd_record.sec_monomer.to_list()
            sec_indexes_strands = map(id2index_strand, sec_ids)
            sec_indexes, sec_strands = zip(*sec_indexes_strands)

            identities = [ident / 100
                          for ident in sd_record.identity.to_list()]
            sec_identities = [ident / 100
                              for ident in sd_record.sec_identity.to_list()]

            reliabilities = get_reliablities(sd_record=sd_record,
                                             identities=identities,
                                             sec_identities=sec_identities)

            monoinstances = []
            for i, st, en, \
                    rel, strand, sec_strand, \
                    mono_index, sec_mono_index, \
                    identity, sec_identity in \
                    zip(count(), starts, ends,
                        reliabilities, strands, sec_strands,
                        indexes, sec_indexes,
                        identities, sec_identities):
                monomer = monomer_db.monomers[mono_index]
                sec_monomer = None
                if sec_mono_index is not None:
                    sec_monomer = monomer_db.monomers[sec_mono_index]
                nucl_segment = nucl_sequence[st:en]
                monoinstance = MonoInstance(monomer=monomer,
                                            sec_monomer=sec_monomer,
                                            strand=strand,
                                            sec_strand=sec_strand,
                                            seq_id=seq_id,
                                            nucl_segment=nucl_segment,
                                            st=st,
                                            en=en,
                                            seq_len=len(nucl_sequence),
                                            reliability=rel,
                                            identity=identity,
                                            sec_identity=sec_identity)
                monoinstances.append(monoinstance)
            return monoinstances

        def reverse_if_needed(monoinstances, nucl_sequence,
                              max_lowercase=0.5):
            is_lowercase = [monoinstance.is_lowercase()
                            for monoinstance in monoinstances
                            if monoinstance.is_reliable()]
            perc_lower_case = np.mean(is_lowercase)
            is_reversed = perc_lower_case > max_lowercase
            if is_reversed:
                # reverse monoinstance
                monoinstances.reverse()
                for monoinstance in monoinstances:
                    monoinstance.reverse()
                # reverse nucl_sequence
                nucl_sequence = RC(nucl_sequence)
            return monoinstances, nucl_sequence, is_reversed

        def get_string(monoinstance):
            string = []
            for monoinstance in monoinstances:
                mono_index = monoinstance.get_monoindex()
                if monoinstance.reliability == Reliability.RELIABLE:
                    if monoinstance.strand == Strand.FORWARD:
                        string.append(mono_index)
                    else:
                        assert monoinstance.strand == Strand.REVERSE
                        string.append(mono_index + monomer_db.get_size())
                else:
                    assert monoinstance.reliability == Reliability.UNRELIABLE
                    string.append(cls.gap_symb)
            string = tuple(string)
            return string

        # logger.debug(f'Constructing raw_monostring for sequence {seq_id}')

        # Trim first and last monomer because they are often unreliable
        sd_record = sd_record[1:-1]

        monoinstances = get_monoinstances(sd_record=sd_record)

        monoinstances, nucl_sequence, is_reversed = \
            reverse_if_needed(monoinstances, nucl_sequence)
        string = get_string(monoinstances)

        # logger.debug(f'Finished construction raw_monostring for seq {seq_id}')
        # logger.debug(f'    length of string = {len(string)}')
        # logger.debug(f'    string: {string}')

        monostring = cls(seq_id=seq_id,
                         monoinstances=monoinstances,
                         raw_monostring=string,
                         nucl_sequence=nucl_sequence,
                         monomer_db=monomer_db,
                         is_reversed=is_reversed)
        return monostring

    def __len__(self):
        return self.raw_monostring.__len__()

    def __getitem__(self, sub):
        if isinstance(sub, slice):
            sublist = self.raw_monostring[sub.start:sub.stop:sub.step]
            return sublist
        return self.raw_monostring[sub]

    def __setitem__(self, sub, item):
        # sub - position
        # item - mono_index
        assert self.raw_monostring[sub] != item
        self.corrections[sub] = (self.raw_monostring[sub], item)
        self.raw_monostring[sub] = item

    def is_corrected(self):
        return len(self.corrections)

    def get_perc_reliable(self):
        is_reliable = [monoinstance.is_reliable()
                       for monoinstance in self.monoinstances]
        perc_reliable = np.mean(is_reliable)
        return perc_reliable

    def get_perc_unreliable(self):
        return 1 - self.get_perc_reliable()

    def get_perc_lowercase(self):
        is_lowercase = [monoinstance.is_lowercase()
                        for monoinstance in self.monoinstances
                        if monoinstance.is_reliable()]
        perc_lowercase = np.mean(is_lowercase)
        return perc_lowercase

    def get_perc_uppercase(self):
        return 1 - self.get_perc_lowercase()

    def classify_monomerinstances(self, only_reliable=True):
        monoindexes = self.monomer_db.get_monoindexes()
        monomerinstances_dict = {monoindex: [] for monoindex in monoindexes}
        for mi in self.monoinstances:
            if (not only_reliable) or (only_reliable and mi.is_reliable()):
                monoindex = mi.get_monoindex()
                monomerinstances_dict[monoindex].append(mi)
        return monomerinstances_dict

    def get_monomerinstances_by_monoindex(self, mono_index,
                                          only_reliable=True):
        monomerinstances_dict = \
            self.classify_monomerinstances(only_reliable=only_reliable)
        return monomerinstances_dict[mono_index]

    def get_nucl_segment(self, st, en):
        assert 0 <= st < en < len(self.nucl_sequence)
        return self.nucl_sequence[st:en]

    def get_kmer_index(self, mink, maxk, positions=True):
        return get_kmer_index_seq(seq=self.raw_monostring,
                                  mink=mink, maxk=maxk,
                                  ignored_chars=set([self.gap_symb]),
                                  positions=positions)

    def get_identities(self):
        identities = []
        for mi in self.monoinstances:
            identities.append(mi.identity)
        return identities

    # def correct_likely_hybrids(self, consensuses):
    #     self.monomer_db.extend_db(consensuses) # TODO uncomment
    #     for i, minst in enumerate(self.monoinstances):
    #         # if not minst.is_reliable(): # TODO uncomment
    #         if self[i] == '?': # TODO remove
    #             continue
    #         mind1 = minst.get_monoindex()
    #         mind2 = minst.get_secmonoindex()
    #         pair = min(mind1, mind2), max(mind1, mind2)
    #         if pair not in consensuses:
    #             continue
    #         consensus = consensuses[pair]
    #         if minst.is_lowercase():
    #             consensus = RC(consensus)
    #         segment = minst.nucl_segment
    #         monomer1 = list(self.monomer_db.get_seqs_by_index(mind1))
    #         monomer2 = list(self.monomer_db.get_seqs_by_index(mind2))
    #         assert len(monomer1) == len(monomer2) == 1
    #         monomer1 = fst_iterable(monomer1)
    #         monomer2 = fst_iterable(monomer2)

    #         ident2monomer1 = calc_identity(segment, monomer1)
    #         ident2monomer2 = calc_identity(segment, monomer2)
    #         ident2consensus = calc_identity(segment, consensus)
    #         if ident2consensus > min(1, 1.05 * max(ident2monomer1, ident2monomer2)):
    #             print(self.seq_id, i, ident2monomer1, ident2monomer2, ident2consensus)
    #             mind = self.monomer_db.id2index[pair]
    #             self[i] = mind

def assert_monostring_validity(monostring):
    string = monostring.raw_monostring
    monomer_db = monostring.monomer_db
    monomer_db_size = monomer_db.get_size()
    monoinsts = monostring.monoinstances
    for i, monoinstance in enumerate(monoinsts):
        mono_index = monoinstance.get_monoindex()
        if monoinstance.strand == Strand.REVERSE:
            mono_index += monomer_db_size
        if monoinstance.reliability == Reliability.RELIABLE:
            assert mono_index == string[i]

    nucl_sequence = monostring.nucl_sequence
    for mi in monoinsts:
        assert nucl_sequence[mi.st:mi.en] == mi.nucl_segment
