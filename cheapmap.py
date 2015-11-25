#
# cheapmap: a simple morph pair mapper
#
# (C) 2015 Hannes H Loeffler, STFC Daresbury
#

from __future__ import print_function

import os
import sys
import glob
import math
import multiprocessing as mp
from functools import partial

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import minimum_spanning_tree
import networkx as nx

import rdkit.Chem as rd
import rdkit.Chem.AllChem as ac
import rdkit.Chem.Draw as draw
from rdkit import DataStructs
from rdkit.Chem.Fingerprints import FingerprintMols
from rdkit.Chem.rdFMCS import FindMCS, AtomCompare, BondCompare



DOT_FILE = 'mst.dot'
GPICKLE_FILE = 'nx_mst.pickle'


# NOTE: the more similar, the smaller the weight must be!
#       0 (or Inf or NaN) means no egde for dense(!) graphs

def tanimoto_score(mol1, mol2):
    fp1 = FingerprintMols.FingerprintMol(mol1)
    fp2 = FingerprintMols.FingerprintMol(mol2)

    return 1.0 / (DataStructs.FingerprintSimilarity(fp1, fp2) + 1e-15)

def maccs_score(mol1, mol2):
    fp1 = rd.MACCSkeys.GenMACCSKeys(mol1)
    fp2 = rd.MACCSkeys.GenMACCSKeys(mol2)

    return 1.0 / (DataStructs.FingerprintSimilarity(fp1, fp2) + 1e-15)


_fmcs_params = dict(maximizeBonds=False, threshold=1.0, timeout=60,
                    verbose=False, matchValences=False,
                    ringMatchesRingOnly=True, completeRingsOnly=True,
                    atomCompare=AtomCompare.CompareAny,
                    bondCompare=BondCompare.CompareAny)

def mcs_score(mol1, mol2):
    mcs = FindMCS( (mol1, mol2), **_fmcs_params)
    pattern = rd.MolFromSmarts(mcs.smartsString)

    # FIXME: deal with multiple matches?
    match1 = mol1.GetSubstructMatch(pattern)

    NA = mol1.GetNumAtoms()
    NB = mol2.GetNumAtoms()
    NMCS = 2 * len(match1)

    # LOMAP, for heavy atoms only
    #beta = 1.0
    #score = math.exp(beta * (NA + NB - NMCS) )

    # simple linear
    score = NA + NB - NMCS + 1

    # Brint&Willet
    #NbA = mol2.GetNumBonds()
    #NbA = mol1.GetNumBonds()
    #mol3 = rd.EditableMol(mol1)

    #for k in range(NA-1, -1, -1):
    #    if k not in match1:
    #        mol3.RemoveAtom(k)

    #NbMCS = mol3.GetMol().GetNumAtoms()

    #score = (float(NMCS + NbMCS) /
    #         float( (NA + NB) * (NbA + NbA) ) )

    return score


valid_methods = {'tanimoto' : tanimoto_score,
                 'maccs' : maccs_score,
                 'mcs' : mcs_score}

def draw_graph(mst, mol_names, dir_names, sim_method):

    mst_a = mst.toarray()
    N = mst.size
    print('\nminimal spanning tree (MST):\n', mst_a)

    cnt = 0
    print('\nsuggested mappings from MST:')

    G = nx.from_scipy_sparse_matrix(mst)

    if sim_method == 'mcs':
        corr = 1
    else:
        corr = 0

    for i, j in zip(mst.nonzero()[0], mst.nonzero()[1]):
        cnt += 1
        n1 = mol_names[i]
        n2 = mol_names[j]
        score = mst_a[i][j]

        print('%6i) %s <> %s (%f)\n' % (cnt, n1, n2, score), end='')

        G.edge[i][j]['label'] = '%.1f' % (score - corr)  # when MCS!
        G.edge[i][j]['len'] = '3.0'

    for n in G.nodes():
        G.node[n]['image'] = os.path.join(dir_names[n],
                                          mol_names[n] + os.extsep + 'svg')
        G.node[n]['shape'] = 'box'
        G.node[n]['label'] = ''

    nx.write_dot(G, DOT_FILE)
    nx.write_gpickle(G, GPICKLE_FILE)


_mol_params = dict(sanitize=False, removeHs=False)

# FIXME: guard against low scores
#        disallow change in total charge
def calc_MST(filenames, sim_method, parallel=False):

    score = valid_methods[sim_method]

    N = len(filenames)
    M = N * (N - 1) / 2
    npout = (M + (100 - M % 100) ) / 100
    simmat = np.zeros(shape=(N,N), dtype=np.float32)

    mols = []
    mol_names = []
    dir_names = []

    print('Reading input files...')

    for filename in filenames:
        mol = rd.MolFromMol2File(filename, **_mol_params)

        dirname = os.path.dirname(filename)
        basename = os.path.splitext(os.path.basename(filename))[0]
        outname = os.path.join(dirname, basename + os.extsep + 'svg')

        mols.append(mol)
        mol_names.append(basename)
        dir_names.append(dirname)

        tmp = ac.Compute2DCoords(mol)

        draw.MolToFile(mol, outname, wedgeBonds=False, size=(150,150),
                       fitImage=True, kekulize=False)

    print('Computing similarity matrix using %s...' % sim_method)

    if parallel:
        #nproc = mp.cpu_count()
        pool = mp.Pool()

        for i in range(N-1):
            print('%s...' % mol_names[i])

            partial_func = partial(score, mols[i])
            simmat[i][i+1:N] = pool.map(partial_func, mols[i+1:N])

        pool.close()
        pool.join()
    else:
        for i in range(N-1):
            print('%s...' % mol_names[i])
            mol1 = mols[i]

            for j in range(i+1, N):
                mol2 = mols[j]
                simmat[i][j] = score(mol1, mol2)

    print('similarity score matrix:\n', simmat)

    # NOTE: this removes edges with the larger weight
    mst = minimum_spanning_tree(csr_matrix(simmat) )

    draw_graph(mst, mol_names, dir_names, sim_method)



if __name__ == '__main__':

    if len(sys.argv) < 3:
        print('Usage: %s similarity[tanimoto|maccs|mcs] '
              'dir_with_mol2 [parallel]' %
              sys.argv[0], file=sys.stderr)
        exit(1)

    # FIXME: other file types
    mol2_files = glob.glob('%s/*.mol2' % sys.argv[2])

    method = sys.argv[1]

    if method not in valid_methods:
        raise ValueError('Unknown similarity method: %s' % method)

    parallel = 0
    if len(sys.argv) == 4:
        parallel = sys.argv[3]

    calc_MST(mol2_files, method, parallel)