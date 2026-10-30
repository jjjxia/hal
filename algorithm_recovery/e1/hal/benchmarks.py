#!/usr/bin/env python3

# CONFIG
import json
import numpy as np
import os
import shutil
import sys
import time

from sklearn.metrics.cluster import normalized_mutual_info_score
from sklearn import metrics
import getpass

hal_path = "/home/jiahao/HardwareRE/dynamicRE/reproduction/functional_dynamic_sota/dynamic/work_hal/" # TODO: adjust absolute path
thread_count = 1 # execution-only resource adaptation; original default was 128

if hal_path == "":
    print("no path provided, aborting...")
    exit()

# if you have mutliple differen hal builds (for example a debug build) you can switch them by setting the environment variable HAL_BUILD_DIR
build_dir = os.getenv('HAL_BUILD_DIR', "build")

# this adds your hal path to you path variable and sets and environment variable HAL_BASE_PATH
sys.path.append(hal_path + build_dir + "/lib/") # this is where your hal python lib is located
os.environ["HAL_BASE_PATH"] = hal_path + build_dir + "/" # hal base path

import hal_py
from hal_plugins import bitorder_propagation
from hal_plugins import dataflow
from hal_plugins import module_identification
from hal_plugins import netlist_preprocessing
from hal_plugins import resynthesis

xilinx_gate_lib = hal_path + "plugins/gate_libraries/definitions/XILINX_UNISIM_hal.hgl"
lattice_gate_lib = hal_path + "plugins/gate_libraries/definitions/ice40ultra_hal.hgl"

aoixm_gate_lib_path = hal_path + "plugins/gate_libraries/definitions/helper_libs/aoixm_hal_i4.hgl"
mux_inv_gl_path = hal_path + "plugins/gate_libraries/definitions/helper_libs/im_hal_i4.hgl"

use_register_ground_truth = True

min_group_size = 8
bitorder_min_group_size = 4

delete_old_results = False

# load benchmarks
benchmarks = json.load(open("benchmarks.json"))
results = {}

result_path = "results/"
if os.path.isdir(result_path):
    if delete_old_results:
        shutil.rmtree(result_path, ignore_errors=True)
        os.mkdir(result_path)
else:
    os.mkdir(result_path)


def find_carry_chains(netlist, vendor):
    vendor_to_carry_pins = {
        "lattice": ("CI", "CO"),
        "xilinx": ("CI", "CO(3)"),
    }

    (ci, co) = vendor_to_carry_pins[vendor]

    carry_chains = []

    # Retrieve all carry gates
    carry_gates = netlist.get_gates(lambda g: g.get_type().has_property(hal_py.c_carry))
    carry_gates_set = set(carry_gates)

    # Collect carry chains until all carry gates have been analyzed
    while carry_gates_set:
        current_gate = carry_gates_set.pop()
        carry_type = current_gate.type

        # Get carry chains by defining appropriate filter function
        carry_chain = hal_py.NetlistUtils.get_gate_chain(current_gate, 
                                                 [carry_type.get_pin_by_name(ci)], 
                                                 [carry_type.get_pin_by_name(co)])

        # Remove shift register gates from candidate set
        for g in carry_chain:
            carry_gates_set.discard(g)

        # Only consider carry chains with more than 2 gates for now
        if len(carry_chain) >= 2:
            carry_chains.append(carry_chain)

    # Check whether a carry chain is a subset of another one
    filtered_carry_chains = []
    for i, c_test in enumerate(carry_chains):
        test_set = set(c_test)

        is_subset = False
        for j, c_other in enumerate(carry_chains):
            if i == j:
                continue

            other_set = set(c_other)
            if test_set.issubset(other_set):
                is_subset = True
                break

        if not is_subset:
            filtered_carry_chains.append(c_test)

    return filtered_carry_chains

def get_groups_of_type(dana_result, gt_property):
    groups = dict()

    for group_id in dana_result:
        new_mux_group = set()
        for gate in dana_result[group_id]:
            if gate.get_type().has_property(gt_property):
                new_mux_group.add(gate)

        if len(new_mux_group) > 0:
            groups[group_id] = new_mux_group   

    return groups

def generate_register_ground_truth(netlist):
    group_name_to_ff = dict()
    for ff in netlist.get_gates(lambda g : g.type.has_property(hal_py.GateTypeProperty.ff)):
        if ("preprocessing_information", "multi_bit_indexed_identifiers") in ff.data:
            _, reconstructed_identifiers_str = ff.get_data("preprocessing_information", "multi_bit_indexed_identifiers")

            reconstructed_identifiers = json.loads(reconstructed_identifiers_str)

            if len(reconstructed_identifiers) == 0:
                group_name = ff.name
            else:
                identifier = reconstructed_identifiers[0]
                group_name = identifier[0]

                if (identifier[2] != "gate_name"):
                    print("Warning, found identifier that stems not from a gate name, this could lead to unwanted behavior")
                    print(reconstructed_identifiers)


            if group_name not in group_name_to_ff:
                group_name_to_ff[group_name] = list()

            group_name_to_ff[group_name].append(ff)

    ground_truth_registers = list()
    for name in group_name_to_ff:
        ground_truth_registers.append(group_name_to_ff[name])

    return ground_truth_registers

def calculate_nmi(ground_truth, prediction):
    ground_truth_gate_id_to_group_id = {}
    prediction_gate_id_to_group_id = {}

    ground_truth_counter = 0
    for idx in range(len(ground_truth)):
        for gate in ground_truth[idx]:
            ground_truth_gate_id_to_group_id[gate.get_id()] = ground_truth_counter
        ground_truth_counter += 1

    prediction_counter = 0
    for cluster in prediction:
        for gate in prediction[cluster]:
            prediction_gate_id_to_group_id[gate.get_id()] = prediction_counter
        prediction_counter += 1

    ground_truth_gate_id_to_group_id = dict(sorted(ground_truth_gate_id_to_group_id.items()))
    prediction_gate_id_to_group_id = dict(sorted(prediction_gate_id_to_group_id.items()))

    if len(ground_truth_gate_id_to_group_id) != len(prediction_gate_id_to_group_id):
        print("[!] error nmi: one group has more gates than the other...")
        print("[!] ground_truth:" +str(len(ground_truth_gate_id_to_group_id)))
        print("[!] prediction:" +str(len(prediction_gate_id_to_group_id)))

    ground_truth_list = []
    prediction_list = []

    for _gate_id, group_id in ground_truth_gate_id_to_group_id.items():
        ground_truth_list.append(group_id)
    
    for _gate_id, group_id in prediction_gate_id_to_group_id.items():
        prediction_list.append(group_id)


    nmi = normalized_mutual_info_score(ground_truth_list, prediction_list)
    contingency_matrix = metrics.cluster.contingency_matrix(ground_truth_list, prediction_list)
    # return purity
    purity = np.sum(np.amax(contingency_matrix, axis=0)) / np.sum(contingency_matrix) 

    return nmi, purity

def calculate_bitorder_accuracy(ground_truth, bitorder_result):
    correct_counter = 0
    total_counter = 0

    for mpg in ground_truth: 
        if mpg in bitorder_result:
            ordered_nets = list(net for net, _ in sorted(bitorder_result[mpg].items(), key=lambda i : i[1]))  
            ground_truth_ordered_nets = list(net for net, _ in sorted(ground_truth[mpg].items(), key=lambda i : i[1]))

            total_counter += 1

            if ordered_nets == ground_truth_ordered_nets:
                correct_counter += 1
            else:
                # print("Bitorder Result: ")
                # for n in ordered_nets:
                #     print("{} / {}".format(n.id, n.name))

                # print("Bitorder Ground Truth: ")
                # for n in ground_truth_ordered_nets:
                #     print("{} / {}".format(n.id, n.name))    
                pass                

    if total_counter == 0:
        return None

    return correct_counter / total_counter

def save_results(benchmark, vendor, benchmark_results): 
    json_path = result_path + "benchmark_results.json"
    
    if os.path.isfile(json_path):
        with open(json_path, "r") as json_file:
            # load result json
            total_results = json.load(json_file)
    else:
        total_results = dict()

    with open(json_path, "w") as json_file: 
        # add benchmark results
        if benchmark not in total_results:
            total_results[benchmark] = dict()

        total_results[benchmark][vendor] = benchmark_results
        
        # write results to json
        json_str = json.dumps(total_results, indent=4)
        json_file.write(json_str)

hal_py.plugin_manager.load_all_plugins()

aoixm_gate_lib = hal_py.GateLibraryManager.load(aoixm_gate_lib_path)
mux_inv_gl = hal_py.GateLibraryManager.load(mux_inv_gl_path)

# run benchmarks
for vendor in benchmarks:
    for benchmark in benchmarks[vendor]:
        benchmark_path = benchmarks[vendor][benchmark]["path"]

        output_hal_file = result_path  + "/" + vendor + "/" + benchmark + ".hal"
        if os.path.isfile(output_hal_file):
            continue

        block_list = [
            #("lattice", "icicle"), 
            # ("xilinx", "icicle"), 
            # ("lattice", "hilbert_transformer"), 
            # ("xilinx", "hilbert_transformer"), 
            # ("lattice", "simple_risc_v"), 
            # ("xilinx", "simple_risc_v"), 
            # ("lattice", "canny_edge_detector"), 
            # ("xilinx", "canny_edge_detector"), 
            # ("lattice", "fft64"),
            # ("xilinx", "fft64"), 
            # ("lattice", "ibex"),
            # ("xilinx", "ibex"),
            # ("lattice", "sha256"),
            # ("xilinx", "sha256")
        ]

        if (vendor, benchmark) in block_list: # or (vendor, benchmark) not in allow_list:
            continue

        start = time.time()

        print("Running Benchmark: {}".format(benchmark))

        if "maggie" in benchmark:
            use_register_ground_truth = False

        if benchmark not in results:
            results[benchmark] = {}

        if vendor not in results[benchmark]:
            results[benchmark][vendor] = {}

        #read netlist
        gate_library_path = ""
        if vendor == "xilinx":
            gate_library_path = xilinx_gate_lib
        elif vendor == "lattice":
            gate_library_path = lattice_gate_lib

        netlist = hal_py.NetlistFactory.load_netlist(benchmark_path, gate_library_path)

        for mod in netlist.get_modules():
            netlist.delete_module(mod)

        benchmark_results = {
            "GENERAL": dict(),
            "MODULE_IDENTIFICATION_I": dict(),
            "MODULE_IDENTIFICATION_II": dict(),
            "DATAFLOW": dict(),
            "MULTIPLEXER": dict(),
            "PREPROCESSING_I": dict(),
            "PREPROCESSING_II": dict(),
            "RESYNTHESIS_I": dict(),
            "RESYNTHESIS_II": dict(),
            "BITORDER_PROPAGATION": dict()
        }

        expected_size = benchmarks[vendor][benchmark]["expected_sizes"]

        #############################
        ###### PREPROCESSING I ######
        #############################

        total_ff_count = len(netlist.get_gates(lambda g: g.get_type().has_property(hal_py.GateTypeProperty.ff)))
        total_lut_count = len(netlist.get_gates(lambda g: g.get_type().has_property(hal_py.GateTypeProperty.c_lut)))
        total_gate_count = len(netlist.get_gates())

        reconstruction_count = netlist_preprocessing.reconstruct_indexed_ff_identifiers(netlist)
        benchmark_results["PREPROCESSING_I"]["reconstruction_count"] = reconstruction_count
        benchmark_results["PREPROCESSING_I"]["total_ff_count"] = total_ff_count
        benchmark_results["PREPROCESSING_I"]["total_lut_count"] = total_lut_count
        benchmark_results["PREPROCESSING_I"]["total_gate_count"] = total_gate_count
        benchmark_results["PREPROCESSING_I"]["relative_reconstruction"] = "{:.2f}".format((reconstruction_count/total_ff_count) if total_ff_count > 0 else 1.0)
        
        multi_bit_modules = netlist_preprocessing.create_multi_bit_gate_modules(netlist, benchmarks[vendor][benchmark]["multi_bit_gate_modules"])
        benchmark_results["PREPROCESSING_I"]["created_multi_bit_modules"] = len(multi_bit_modules)
        benchmark_results["PREPROCESSING_I"]["reconstructed_top_module_pin_groups"] = netlist_preprocessing.reconstruct_top_module_pin_groups(netlist)
        
        iteration = 0
        while True:
            iteration_res = dict()

            iteration_res["removed_unconnceted_gates"]  = netlist_preprocessing.remove_unconnected_gates(netlist)
            iteration_res["removed_unconnceted_nets"] = netlist_preprocessing.remove_unconnected_nets(netlist)
            iteration_res["propagated_constants"] = netlist_preprocessing.propagate_constants(netlist)
            iteration_res["simplified_lut_inits"] = netlist_preprocessing.simplify_lut_inits(netlist)
            iteration_res["removed_lut_end_points"] = netlist_preprocessing.remove_unused_lut_inputs(netlist)
            iteration_res["removed_buffers"] = netlist_preprocessing.remove_buffers(netlist)
            iteration_res["removed_redundant_gates"] = netlist_preprocessing.remove_redundant_gates(netlist)
            iteration_res["removed_redundant_loops"] = netlist_preprocessing.remove_redundant_loops(netlist)
            iteration_res["clean_up_loop"] = netlist_preprocessing.remove_unconnected_looped(netlist)

            if sum(iteration_res.values()) == 0:
                break
            
            benchmark_results["PREPROCESSING_I"]["iteration_" + str(iteration)] = iteration_res

        #############################
        ##### INITIAL DATAFLOW ######
        #############################

        ground_truth_registers = generate_register_ground_truth(netlist)

        initial_identified_module_known_structures = list()

        for mod in netlist.get_modules():
            pg_list = list()
            for pg in mod.get_pin_groups():
                if pg.name not in ["I", "O", "CTRL"] and pg.size() > 1:
                    pg_list.append(pg)
            
            initial_identified_module_known_structures.append((mod, pg_list))

        print("Initial known structures:")
        for (m, pg_list) in initial_identified_module_known_structures:
            for pg in pg_list:
                print("{} - {} : {}".format(m.name, m.id, pg.name))
                

        carry_chains = find_carry_chains(netlist, vendor)
        carry_chain_mods = list()
        print("Creating carry chains for initial dana run")
        counter = 0
        for chain in carry_chains:
            mod = netlist.create_module("CARRY_CHAIN_" + str(counter), netlist.get_top_module(), chain)
            in_pg = mod.create_pin_group("IN", mod.get_input_pins(), hal_py.PinDirection.input, hal_py.PinType.data)
            out_pg = mod.create_pin_group("OUT", mod.get_output_pins(), hal_py.PinDirection.output, hal_py.PinType.data)
            carry_chain_mods.append((mod, [in_pg, out_pg]))
            counter += 1

        print("Created " + str(len(carry_chain_mods)) + " carry chain mods")

        initial_dana_regs = list()
        if use_register_ground_truth:
            initial_dana_regs = ground_truth_registers
        else:
            config = dataflow.Configuration(netlist)
            config.with_min_group_size(min_group_size)
            config.with_gate_types({hal_py.GateTypeProperty.ff})
            config.with_expected_sizes(expected_size)
            config.with_known_structures(initial_identified_module_known_structures)
            config.with_known_structures(carry_chain_mods)
            config.with_control_pin_types({hal_py.PinType.clock, hal_py.PinType.enable, hal_py.PinType.reset, hal_py.PinType.set})

            intial_dana_result = dataflow.analyze(config)
            intial_dana_result_registers = intial_dana_result.get_groups()

            for id in intial_dana_result_registers:
                initial_dana_regs.append(list(intial_dana_result_registers[id]))
                
        # remove initial carry chains again
        for (mod, _pgs) in carry_chain_mods:
            netlist.delete_module(mod)

        ####################################################################################
        ### MODULE IDENTIFICATION I - Finding the best iteration for each base candidate ###
        ####################################################################################

        initial_netlist = netlist.copy()

        translated_initial_dana_regs = list()
        for reg in initial_dana_regs:
            translated_reg = list()
            for gate in reg:
                translated_reg.append(initial_netlist.get_gate_by_id(gate.id))

            translated_initial_dana_regs.append(reg)

        initial_config = module_identification.Configuration(initial_netlist)
        initial_config.max_control_signals = benchmarks[vendor][benchmark]["max_control_signals"]
        initial_config.known_registers = translated_initial_dana_regs
        initial_config.multithreading_priority = module_identification.MultithreadingPriority.time_priority
        initial_config.max_thread_count = thread_count

        initial_config.types_to_check = [
            module_identification.CandidateType.adder, 
            module_identification.CandidateType.constant_multiplication, 
            module_identification.CandidateType.counter, 
            module_identification.CandidateType.absolute, 
            module_identification.CandidateType.equal, 
            module_identification.CandidateType.less_equal, 
            module_identification.CandidateType.value_check,
        ]

        perfectly_identified_base_candidates = list()

        # 1) with no resynthesis

        #TODO maybe remove
        #intermediate_hal_file = result_path  + "/" + vendor + "/" + benchmark + "MI_ORG_" + ".hal"
        #hal_py.NetlistSerializer.serialize_to_file(initial_netlist, intermediate_hal_file)

        res_1 = module_identification.execute(initial_config)

        for id in res_1.get_verified_candidates():
            c = res_1.get_candidate_by_id(id)
            if c.is_verified() and (len(c.control_signals) == 0) and (list(c.types)[0] not in [module_identification.CandidateType.value_check]):
                perfectly_identified_base_candidates.append(set(c.base_gates))

        benchmark_results["MODULE_IDENTIFICATION_I"]["vanilla"] = dict()
        for id in res_1.get_candidates():
            c = res_1.get_candidate_by_id(id)
            if c.is_verified():
                base_name = str(list(c.types)[0])
            else:
                base_name = "CARRY_CHAIN"

            if base_name not in benchmark_results["MODULE_IDENTIFICATION_I"]["vanilla"]:
                benchmark_results["MODULE_IDENTIFICATION_I"]["vanilla"][base_name] = 0
            
            benchmark_results["MODULE_IDENTIFICATION_I"]["vanilla"][base_name] += 1
        benchmark_results["MODULE_IDENTIFICATION_I"]["vanilla"]["timings"] = json.loads(res_1.get_timing_stats())

        nets_missing_source = initial_netlist.get_nets(lambda n : (n.get_num_of_sources() == 0) and not n.is_global_input_net())
        nets_missing_destination = initial_netlist.get_nets(lambda n : (n.get_num_of_destinations() == 0) and not n.is_global_output_net())
        multi_driven_nets = initial_netlist.get_nets(lambda n : n.get_num_of_sources() > 1)

        benchmark_results["RESYNTHESIS_I"]["initial_missing_sources"] = len(nets_missing_source)
        benchmark_results["RESYNTHESIS_I"]["initial_missing_destinations"] = len(nets_missing_destination)
        benchmark_results["RESYNTHESIS_I"]["initial_multi_driven_nets"] = len(multi_driven_nets)

        # 2) with gate resynthesis (exclude already classified gates)

        resynth_gates_set = set()
        for gate in initial_netlist.get_gates(lambda g : g.type.has_property(hal_py.GateTypeProperty.c_lut)):
            resynth_gates_set.add(gate)

        benchmark_results["RESYNTHESIS_I"]["gate_resynthesis_count"] = resynthesis.resynthesize_gates(initial_netlist, list(resynth_gates_set), aoixm_gate_lib)
        benchmark_results["RESYNTHESIS_I"]["after_first-clean_up_loop"] = netlist_preprocessing.remove_unconnected_looped(initial_netlist)

        # TODO maybe remove
        #intermediate_hal_file = result_path  + "/" + vendor + "/" + benchmark + "MI_GATE_RESYNTH_" + ".hal"
        #hal_py.NetlistSerializer.serialize_to_file(initial_netlist, intermediate_hal_file)

        nets_missing_source = initial_netlist.get_nets(lambda n : (n.get_num_of_sources() == 0) and not n.is_global_input_net())
        nets_missing_destination = initial_netlist.get_nets(lambda n : (n.get_num_of_destinations() == 0) and not n.is_global_output_net())
        multi_driven_nets = initial_netlist.get_nets(lambda n : n.get_num_of_sources() > 1)

        benchmark_results["RESYNTHESIS_I"]["after_first-missing_sources"] = len(nets_missing_source)
        benchmark_results["RESYNTHESIS_I"]["after_first-missing_destinations"] = len(nets_missing_destination)
        benchmark_results["RESYNTHESIS_I"]["after_first-multi_driven_nets"] = len(multi_driven_nets)

        initial_config = initial_config.with_blocked_base_candidates(perfectly_identified_base_candidates)

        res_2 = module_identification.execute(initial_config)

        for id in res_2.get_verified_candidates():
            c = res_2.get_candidate_by_id(id)
            if c.is_verified() and (len(c.control_signals) == 0) and (list(c.types)[0] not in [module_identification.CandidateType.value_check]):
                perfectly_identified_base_candidates.append(set(c.gates))

        benchmark_results["MODULE_IDENTIFICATION_I"]["gate_resynth"] = dict()
        for id in res_2.get_candidates():
            c = res_2.get_candidate_by_id(id)
            if c.is_verified():
                base_name = str(list(c.types)[0])
            else:
                base_name = "CARRY_CHAIN"

            if base_name not in benchmark_results["MODULE_IDENTIFICATION_I"]["gate_resynth"]:
                benchmark_results["MODULE_IDENTIFICATION_I"]["gate_resynth"][base_name] = 0
            
            benchmark_results["MODULE_IDENTIFICATION_I"]["gate_resynth"][base_name] += 1

        benchmark_results["MODULE_IDENTIFICATION_I"]["gate_resynth"]["timings"] = json.loads(res_2.get_timing_stats())

        # 3) with subgraph resynthesis (exclude already classified gates)
        resynth_gates_set = set()
        for gate in initial_netlist.get_gates(lambda g : (g.type.has_property(hal_py.GateTypeProperty.c_lut) or "HAL_" in g.type.name) and not g.is_gnd_gate() and not g.is_vcc_gate()):
            resynth_gates_set.add(gate)
                
        # TODO this crashes, find out why
        if (vendor, benchmark) not in [("xilinx", "fft64"), ("lattice", "fft64"), ("lattice", "canny_edge_detector")]:
            benchmark_results["RESYNTHESIS_I"]["subgraph_resynthesis_count"] = resynthesis.resynthesize_subgraph(initial_netlist, list(resynth_gates_set), aoixm_gate_lib)

        benchmark_results["RESYNTHESIS_I"]["after_second-clean_up_loop"] = netlist_preprocessing.remove_unconnected_looped(initial_netlist)

        # TODO maybe remove
        # intermediate_hal_file = result_path  + "/" + vendor + "/" + benchmark + "MI_SUB_RESYNTH_" + ".hal"
        # hal_py.NetlistSerializer.serialize_to_file(initial_netlist, intermediate_hal_file)

        nets_missing_source = initial_netlist.get_nets(lambda n : (n.get_num_of_sources() == 0) and not n.is_global_input_net())
        nets_missing_destination = initial_netlist.get_nets(lambda n : (n.get_num_of_destinations() == 0) and not n.is_global_output_net())
        multi_driven_nets = initial_netlist.get_nets(lambda n : n.get_num_of_sources() > 1)

        benchmark_results["RESYNTHESIS_I"]["after_second-missing_sources"] = len(nets_missing_source)
        benchmark_results["RESYNTHESIS_I"]["after_second-missing_destinations"] = len(nets_missing_destination)
        benchmark_results["RESYNTHESIS_I"]["after_second-multi_driven_nets"] = len(multi_driven_nets)
        
        initial_config = initial_config.with_blocked_base_candidates(perfectly_identified_base_candidates)

        res_3 = module_identification.execute(initial_config)

        benchmark_results["MODULE_IDENTIFICATION_I"]["subgraph_resynth"] = dict()
        for id in res_3.get_candidates():
            c = res_3.get_candidate_by_id(id)
            if c.is_verified():
                base_name = str(list(c.types)[0])
            else:
                base_name = "CARRY_CHAIN"

            if base_name not in benchmark_results["MODULE_IDENTIFICATION_I"]["subgraph_resynth"]:
                benchmark_results["MODULE_IDENTIFICATION_I"]["subgraph_resynth"][base_name] = 0
            
            benchmark_results["MODULE_IDENTIFICATION_I"]["subgraph_resynth"][base_name] += 1
        
        benchmark_results["MODULE_IDENTIFICATION_I"]["subgraph_resynth"]["timings"] = json.loads(res_3.get_timing_stats())

        block_lists = module_identification.Result.assign_base_candidates_to_iterations([res_1, res_2, res_3], True)

        translated_block_lists = list()
        for iteration in block_lists:
            new_iteration = list()
            for block_list in iteration:
                new_block_list = set()
                for gate in block_list:
                    new_gate = netlist.get_gate_by_id(gate.id)
                    new_block_list.add(new_gate)

                new_iteration.append(new_block_list.copy())

            translated_block_lists.append(new_iteration.copy())

        ################################################################################
        ### MODULE IDENTIFICATION II - Finding the best candidates in each iteration ###
        ################################################################################

        config = module_identification.Configuration(netlist)
        config.max_control_signals = benchmarks[vendor][benchmark]["max_control_signals"]
        config.known_registers = initial_dana_regs
        config.multithreading_priority = module_identification.MultithreadingPriority.time_priority
        config.max_thread_count = thread_count


        config.types_to_check = [
            module_identification.CandidateType.adder, 
            module_identification.CandidateType.constant_multiplication, 
            module_identification.CandidateType.absolute, 
            module_identification.CandidateType.counter, 
            module_identification.CandidateType.equal, 
            module_identification.CandidateType.less_equal, 
            module_identification.CandidateType.value_check,
        ]

        identified_modules = list()
        identified_gates = list()

        # 1) with no resynthesis

        config = config.with_blocked_base_candidates(translated_block_lists[0])
        res_1 = module_identification.execute(config)

        for id in res_1.get_candidates():
            c = res_1.get_candidate_by_id(id)
            if c.is_verified():
                identified_modules.append(c.gates)
                identified_gates.extend(c.gates)

        benchmark_results["MODULE_IDENTIFICATION_II"]["vanilla"] = dict()
        for id in res_1.get_candidates():
            c = res_1.get_candidate_by_id(id)
            if c.is_verified():
                base_name = str(list(c.types)[0])
            else:
                base_name = "CARRY_CHAIN"

            if base_name not in benchmark_results["MODULE_IDENTIFICATION_II"]["vanilla"]:
                benchmark_results["MODULE_IDENTIFICATION_II"]["vanilla"][base_name] = 0
            
            benchmark_results["MODULE_IDENTIFICATION_II"]["vanilla"][base_name] += 1

        nets_missing_source = netlist.get_nets(lambda n : (n.get_num_of_sources() == 0) and not n.is_global_input_net())
        nets_missing_destination = netlist.get_nets(lambda n : (n.get_num_of_destinations() == 0) and not n.is_global_output_net())
        multi_driven_nets = netlist.get_nets(lambda n : n.get_num_of_sources() > 1)

        benchmark_results["RESYNTHESIS_II"]["initial_missing_sources"] = len(nets_missing_source)
        benchmark_results["RESYNTHESIS_II"]["initial_missing_destinations"] = len(nets_missing_destination)
        benchmark_results["RESYNTHESIS_II"]["initial_multi_driven_nets"] = len(multi_driven_nets)

        # 2) with gate resynthesis (exclude already classified gates)

        resynth_gates_set = set()
        for gate in netlist.get_gates(lambda g : g.type.has_property(hal_py.GateTypeProperty.c_lut)):
            if gate not in identified_gates:
               resynth_gates_set.add(gate)

        benchmark_results["RESYNTHESIS_II"]["gate_resynthesis_count"] = resynthesis.resynthesize_gates(netlist, list(resynth_gates_set), aoixm_gate_lib)
        benchmark_results["RESYNTHESIS_II"]["after_first-clean_up_loop"] = netlist_preprocessing.remove_unconnected_looped(netlist)

        nets_missing_source = netlist.get_nets(lambda n : (n.get_num_of_sources() == 0) and not n.is_global_input_net())
        nets_missing_destination = netlist.get_nets(lambda n : (n.get_num_of_destinations() == 0) and not n.is_global_output_net())
        multi_driven_nets = netlist.get_nets(lambda n : n.get_num_of_sources() > 1)

        benchmark_results["RESYNTHESIS_II"]["after_first-missing_sources"] = len(nets_missing_source)
        benchmark_results["RESYNTHESIS_II"]["after_first-missing_destinations"] = len(nets_missing_destination)
        benchmark_results["RESYNTHESIS_II"]["after_first-multi_driven_nets"] = len(multi_driven_nets)

        config = config.with_blocked_base_candidates(translated_block_lists[1])

        res_2 = module_identification.execute(config)

        for id in res_2.get_candidates():
            c = res_2.get_candidate_by_id(id)
            if c.is_verified():
                identified_modules.append(c.gates)
                identified_gates.extend(c.gates)

        benchmark_results["MODULE_IDENTIFICATION_II"]["gate_resynth"] = dict()
        for id in res_2.get_candidates():
            c = res_2.get_candidate_by_id(id)
            if c.is_verified():
                base_name = str(list(c.types)[0])
            else:
                base_name = "CARRY_CHAIN"

            if base_name not in benchmark_results["MODULE_IDENTIFICATION_II"]["gate_resynth"]:
                benchmark_results["MODULE_IDENTIFICATION_II"]["gate_resynth"][base_name] = 0
            
            benchmark_results["MODULE_IDENTIFICATION_II"]["gate_resynth"][base_name] += 1

        # 3) with subgraph resynthesis (exclude already classified gates)

        resynth_gates_set = set()
        for gate in netlist.get_gates(lambda g : (g.type.has_property(hal_py.GateTypeProperty.c_lut) or "HAL_" in g.type.name) and not g.is_gnd_gate() and not g.is_vcc_gate()):
            if gate not in identified_gates:
                resynth_gates_set.add(gate)

        # TODO this crashes, find out why
        if (vendor, benchmark) not in [("xilinx", "fft64"), ("lattice", "fft64"), ("lattice", "canny_edge_detector")]:
            benchmark_results["RESYNTHESIS_II"]["subgraph_resynthesis_count"] = resynthesis.resynthesize_subgraph(netlist, list(resynth_gates_set), aoixm_gate_lib)

        benchmark_results["RESYNTHESIS_II"]["after_second-clean_up_loop"] = netlist_preprocessing.remove_unconnected_looped(netlist)

        nets_missing_source = netlist.get_nets(lambda n : (n.get_num_of_sources() == 0) and not n.is_global_input_net())
        nets_missing_destination = netlist.get_nets(lambda n : (n.get_num_of_destinations() == 0) and not n.is_global_output_net())
        multi_driven_nets = netlist.get_nets(lambda n : n.get_num_of_sources() > 1)

        benchmark_results["RESYNTHESIS_II"]["after_second-missing_sources"] = len(nets_missing_source)
        benchmark_results["RESYNTHESIS_II"]["after_second-missing_destinations"] = len(nets_missing_destination)
        benchmark_results["RESYNTHESIS_II"]["after_second-multi_driven_nets"] = len(multi_driven_nets)

        config = config.with_blocked_base_candidates(translated_block_lists[2])
        
        res_3 = module_identification.execute(config)
        
        for id in res_3.get_candidates():
            c = res_3.get_candidate_by_id(id)
            if c.is_verified():
                identified_modules.append(c.gates)
                identified_gates.extend(c.gates)

        benchmark_results["MODULE_IDENTIFICATION_II"]["subgraph_resynth"] = dict()
        for id in res_3.get_candidates():
            c = res_3.get_candidate_by_id(id)
            if c.is_verified():
                base_name = str(list(c.types)[0])
            else:
                base_name = "CARRY_CHAIN"

            if base_name not in benchmark_results["MODULE_IDENTIFICATION_II"]["subgraph_resynth"]:
                benchmark_results["MODULE_IDENTIFICATION_II"]["subgraph_resynth"][base_name] = 0
            
            benchmark_results["MODULE_IDENTIFICATION_II"]["subgraph_resynth"][base_name] += 1
            
 
        res = res_1.merge(res_2, initial_dana_regs).merge(res_3, initial_dana_regs)

        res.create_modules_in_netlist()

        # delete carry chains that are very small and likely do not compute arithmetic
        if (vendor == "lattice"):
            for m in netlist.get_modules():
                if "CARRY_CHAIN" in m.name and len(m.gates) < 4:
                    netlist.delete_module(m)

        identified_modules = list()
        for m in netlist.get_modules():
            if m.has_data("ModuleIdentification", "VERIFIED_TYPES"):
                identified_modules.append(m.gates)

        benchmark_results["MODULE_IDENTIFICATION_II"]["final"] = dict()
        for mod in netlist.get_modules():
            if not mod.has_data("ModuleIdentification", "VERIFIED_TYPES"):
                continue

            name = mod.get_name()
            base_name = name[:name.rfind("_")]

            if base_name not in benchmark_results["MODULE_IDENTIFICATION_II"]["final"]:
                benchmark_results["MODULE_IDENTIFICATION_II"]["final"][base_name] = 0
            
            benchmark_results["MODULE_IDENTIFICATION_II"]["final"][base_name] += 1

        benchmark_results["MODULE_IDENTIFICATION_II"]["final_alt"] = dict()
        for id in res.get_candidates():
            c = res.get_candidate_by_id(id)
            if c.is_verified():
                if len(c.types) == 0:
                    print("WARNING weird behavior!")
                    base_name = "WEIRD"
                else:
                    base_name = str(list(c.types)[0])
            else:
                base_name = "CARRY_CHAIN"

            if base_name not in benchmark_results["MODULE_IDENTIFICATION_II"]["final_alt"]:
                benchmark_results["MODULE_IDENTIFICATION_II"]["final_alt"][base_name] = 0
            
            benchmark_results["MODULE_IDENTIFICATION_II"]["final_alt"][base_name] += 1

        #############################
        ##### PREPROCESSING II ######
        #############################

        iteration = 0
        while True:
            iteration_res = dict()

            iteration_res["removed_unconnceted_gates"]  = netlist_preprocessing.remove_unconnected_gates(netlist)
            iteration_res["removed_unconnceted_nets"] = netlist_preprocessing.remove_unconnected_nets(netlist)
            iteration_res["propagated_constants"] = netlist_preprocessing.propagate_constants(netlist)
            iteration_res["simplified_lut_inits"] = netlist_preprocessing.simplify_lut_inits(netlist)
            iteration_res["removed_lut_end_points"] = netlist_preprocessing.remove_unused_lut_inputs(netlist)
            iteration_res["removed_buffers"] = netlist_preprocessing.remove_buffers(netlist)
            # Omit removal of redundant gates in the second preprocessing, since this optimizes the cloned gates away. In the long run we should add a allow/block list to the preprocessing functions
            iteration_res["manual_mux_optimizations"] = netlist_preprocessing.manual_mux_optimizations(netlist, mux_inv_gl)
            iteration_res["clean_up_loop"] = netlist_preprocessing.remove_unconnected_looped(netlist)
            
            if sum(iteration_res.values()) == 0:
                break
            
            benchmark_results["PREPROCESSING_II"]["iteration_" + str(iteration)] = iteration_res


        total_combinational = netlist.get_gates(lambda g : (g.type.has_property(hal_py.GateTypeProperty.combinational) and not g.is_gnd_gate() and not g.is_vcc_gate()))

        total_classified = set()
        for gate in netlist.get_gates(lambda g : (g.type.has_property(hal_py.GateTypeProperty.combinational) and not g.is_gnd_gate() and not g.is_vcc_gate())):
            if gate.get_module().has_data("ModuleIdentification", "VERIFIED_TYPES"):
                total_classified.add(gate)

        total_classified_with_mux = set()
        for gate in netlist.get_gates(lambda g : (g.type.has_property(hal_py.GateTypeProperty.combinational) and not g.is_gnd_gate() and not g.is_vcc_gate())):
            if gate.get_module().has_data("ModuleIdentification", "VERIFIED_TYPES"):
                total_classified_with_mux.add(gate)
            elif gate.type.has_property(hal_py.c_mux):
                total_classified_with_mux.add(gate)

        benchmark_results["MODULE_IDENTIFICATION_II"]["absolute_combinational"] = len(total_combinational)
        benchmark_results["MODULE_IDENTIFICATION_II"]["absolute_classified"] = len(total_classified)
        benchmark_results["MODULE_IDENTIFICATION_II"]["absolute_classified_with_mux"] = len(total_classified_with_mux)

        if (total_combinational):
            benchmark_results["MODULE_IDENTIFICATION_II"]["relative_classified"] = "{:.2}".format(len(total_classified) / len(total_combinational))
            benchmark_results["MODULE_IDENTIFICATION_II"]["relative_classified_with_mux"] = "{:.2}".format(len(total_classified_with_mux) / len(total_combinational))

        #############################
        ######### DATAFLOW ##########
        #############################

        ground_truth_registers = generate_register_ground_truth(netlist)

        identified_module_known_structures = list()

        for mod in netlist.get_modules():
            pg_list = list()
            for pg in mod.get_pin_groups():
                if pg.name not in ["I", "O", "CTRL"] and pg.size() > 1:
                    pg_list.append(pg)
            
            identified_module_known_structures.append((mod, pg_list))

        # TODO remove
        # print("Known structures for DANA:")
        # for (m, pg_list) in identified_module_known_structures:
        #     for pg in pg_list:
        #         print("\t{} - {} : {}".format(m.name, m.id, pg.name))

        # DANA new: Known structures and Sizes
        config_new = dataflow.Configuration(netlist)
        config_new.with_min_group_size(min_group_size)
        config_new.with_gate_types({hal_py.GateTypeProperty.ff})
        config_new.with_control_pin_types({hal_py.PinType.clock, hal_py.PinType.enable, hal_py.PinType.reset, hal_py.PinType.set})
        config_new.with_expected_sizes(expected_size)
        config_new.with_known_structures(identified_module_known_structures)

        dana_result_new = dataflow.analyze(config_new)
        dana_result_registers_new = dana_result_new.get_groups()

        register_sizes_new = dict()
        for _id, reg in dana_result_registers_new.items():
            if len(reg) not in register_sizes_new:
                register_sizes_new[len(reg)] = 0

            register_sizes_new[len(reg)] += 1

        register_sizes_new = dict(sorted(register_sizes_new.items()))

        nmi_new, purity_new = calculate_nmi(ground_truth_registers, dana_result_registers_new)

        benchmark_results["DATAFLOW"]["New"] = {}
        benchmark_results["DATAFLOW"]["New"]["nmi"] = nmi_new
        benchmark_results["DATAFLOW"]["New"]["purity"] = purity_new
        benchmark_results["DATAFLOW"]["New"]["register_sizes"] = register_sizes_new

        # DANA old: run dana normal just for FFs (original version) and expected sizes
        config = dataflow.Configuration(netlist)
        config.with_min_group_size(min_group_size)
        config.with_gate_types({hal_py.GateTypeProperty.ff})
        config.with_expected_sizes(expected_size)
        config.with_control_pin_types({hal_py.PinType.clock, hal_py.PinType.enable, hal_py.PinType.reset, hal_py.PinType.set})

        dana_result = dataflow.analyze(config)
        dana_result_registers = dana_result.get_groups()

        register_sizes = dict()
        for _id, reg in dana_result_registers.items():
            if len(reg) not in register_sizes:
                register_sizes[len(reg)] = 0

            register_sizes[len(reg)] += 1

        register_sizes = dict(sorted(register_sizes.items()))

        nmi, purity = calculate_nmi(ground_truth_registers, dana_result_registers)

        benchmark_results["DATAFLOW"]["Original"] = {}
        benchmark_results["DATAFLOW"]["Original"]["nmi"] = nmi
        benchmark_results["DATAFLOW"]["Original"]["purity"] = purity
        benchmark_results["DATAFLOW"]["Original"]["register_sizes"] = register_sizes

        if use_register_ground_truth:
            final_registers = ground_truth_registers
        else:
            final_registers = dana_result_new.get_groups()

        #############################
        ######## MULTIPLEXER ########
        #############################

        identified_module_known_structures = list()

        for mod in netlist.get_modules():
            pg_list = list()
            for pg in mod.get_pin_groups():
                if pg.name not in ["I", "O"] and pg.size() > min_group_size:
                    pg_list.append(pg)
            
            identified_module_known_structures.append((mod, pg_list))

        # expected_sizes and registers and known groups and modules as structures
        config = dataflow.Configuration(netlist)
        config.with_min_group_size(min_group_size)
        config.with_known_groups(final_registers)
        config.with_known_structures(identified_module_known_structures)
        config.with_gate_types({hal_py.GateTypeProperty.ff, hal_py.GateTypeProperty.c_mux})
        config.with_expected_sizes(expected_size)
        config.with_control_pin_types({hal_py.PinType.clock, hal_py.PinType.enable, hal_py.PinType.reset, hal_py.PinType.set, hal_py.PinType.select})

        dana_multiplexer_result = dataflow.analyze(config)
        dana_multiplexer_result_groups = dana_multiplexer_result.get_groups()
        mux_groups = get_groups_of_type(dana_multiplexer_result_groups, hal_py.GateTypeProperty.c_mux)

        multiplexer_sizes = dict()
        for _id, mux in mux_groups.items():
            if len(mux) not in multiplexer_sizes:
                multiplexer_sizes[len(mux)] = 0

            multiplexer_sizes[len(mux)] += 1

        multiplexer_sizes = dict(sorted(multiplexer_sizes.items()))

        benchmark_results["MULTIPLEXER"]["New"] = {}
        benchmark_results["MULTIPLEXER"]["New"]["multiplexer_sizes"] = multiplexer_sizes

        final_multiplexers = mux_groups
        
        #############################
        ######### BITORDER ##########
        #############################

        # this is basically only here to create modules with the correct pingroups
        config = dataflow.Configuration(netlist)
        config.with_min_group_size(min_group_size)
        config.with_known_groups(final_registers)
        config.with_gate_types({hal_py.GateTypeProperty.ff})
        config.with_expected_sizes(expected_size)
        config.with_control_pin_types({hal_py.PinType.clock, hal_py.PinType.enable, hal_py.PinType.reset, hal_py.PinType.set, hal_py.PinType.select})

        final_dana_result = dataflow.analyze(config)

        filtered_ids = set()
        groups = final_dana_result.get_groups()
        for id in groups:
            gates = groups[id]
            if len(gates) > 2:
                filtered_ids.add(id)

        final_modules = final_dana_result.create_modules(group_ids=filtered_ids)

        unknown_module_pingroups = list()
        known_module_pingroups = list()

        use_register_ground_truth = True

        for m in netlist.get_modules():
            if "DANA" in m.name:
                for pg in m.get_pin_groups():
                    if pg.size() > bitorder_min_group_size:
                        unknown_module_pingroups.append((m, pg))

                continue

            # value checks do not provide a bitorder
            if "VALUE_CHECK" in m.name:
                continue

            for pg in m.get_pin_groups():
                if pg.name not in ["I", "O"] and (pg.size() > bitorder_min_group_size):
                    known_module_pingroups.append((m, pg))   

        # generate ground truth for register modules
        bitorder_ground_truth = dict()
        if use_register_ground_truth:
            for m, pg in unknown_module_pingroups:
                net_to_index = dict()
                for p in pg.pins:
                    if p.direction == hal_py.PinDirection.output:
                        gate = p.net.get_sources()[0].gate
                    else:
                        gate = p.net.get_destinations(lambda ep : ep.gate.module == m)[0].gate

                    if ("preprocessing_information", "multi_bit_indexed_identifiers") in gate.data:
                        _, reconstructed_identifiers_str = gate.get_data("preprocessing_information", "multi_bit_indexed_identifiers")

                        reconstructed_identifiers = json.loads(reconstructed_identifiers_str)

                        if len(reconstructed_identifiers) == 0:
                            group_name = gate.name
                            index = 0
                        else:
                            identifier = reconstructed_identifiers[0]
                            group_name = identifier[0]
                            index = identifier[1]    
            
                    net_to_index[p.net] = index

                bitorder_ground_truth[(m, pg)] = net_to_index

        print("KNOWN MODULE BITORDER:")
        for mpg in known_module_pingroups:
            m, pg = mpg
            print("\t{} - {}".format(m.name, pg.name))

        print("UNKNOWN MODULE BITORDER:")
        for mpg in unknown_module_pingroups:
            m, pg = mpg
            print("\t{} - {}".format(m.name, pg.name))

        # 1) only registers
        bitorder_propagation_result = bitorder_propagation.propagate_bitorder(known_module_pingroups, unknown_module_pingroups)

        relative_unknown = 1.0
        if len(unknown_module_pingroups) != 0:
            relative_unknown = (len(bitorder_propagation_result) - len(known_module_pingroups)) / len(unknown_module_pingroups)

        relative_total = 1.0
        if (len(unknown_module_pingroups) + len(known_module_pingroups)) != 0:
            relative_total = len(bitorder_propagation_result) /(len(unknown_module_pingroups) + len(known_module_pingroups))

        known_at_start = 1.0
        if (len(unknown_module_pingroups) + len(known_module_pingroups)) != 0:
            known_at_start = len(known_module_pingroups) / (len(unknown_module_pingroups) + len(known_module_pingroups))

        benchmark_results["BITORDER_PROPAGATION"] = dict()
        benchmark_results["BITORDER_PROPAGATION"]["New"] = dict()
        benchmark_results["BITORDER_PROPAGATION"]["New"]["initial_known_pingroups"] = len(known_module_pingroups)
        benchmark_results["BITORDER_PROPAGATION"]["New"]["initial_unknown_pingroups"] = len(unknown_module_pingroups)
        benchmark_results["BITORDER_PROPAGATION"]["New"]["final_known_pingroups"] = len(bitorder_propagation_result)
        benchmark_results["BITORDER_PROPAGATION"]["New"]["relative_known_initial"] = "{:.2}".format(known_at_start)
        benchmark_results["BITORDER_PROPAGATION"]["New"]["relative_unknown"] = "{:.2}".format(relative_unknown)
        benchmark_results["BITORDER_PROPAGATION"]["New"]["relative_total"] = "{:.2}".format(relative_total)

        if use_register_ground_truth:
            benchmark_results["BITORDER_PROPAGATION"]["New"]["correct_register_pingroups"] = calculate_bitorder_accuracy(bitorder_ground_truth, bitorder_propagation_result)

        for m, pg in bitorder_propagation_result:
            m.set_pin_group_name(pg, pg.name + "_ordered")

        benchmark_results["GENERAL"]["duration"] = "{:.2} min".format((time.time() - start) / 60)

        # Save benchmark results
        results[benchmark][vendor] = benchmark_results
        save_results(benchmark, vendor, benchmark_results)

        output_hal_file = result_path  + "/" + vendor + "/" + benchmark + ".hal"
        hal_py.NetlistSerializer.serialize_to_file(netlist, output_hal_file)

#unload everything hal related
hal_py.plugin_manager.unload_all_plugins()

print("Done with evaluation")
