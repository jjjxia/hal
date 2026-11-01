#include "algorithm_recovery/plugin_algorithm_recovery.h"

namespace hal
{
    extern std::unique_ptr<BasePluginInterface> create_plugin_instance()
    {
        return std::make_unique<AlgorithmRecoveryPlugin>();
    }

    std::string AlgorithmRecoveryPlugin::get_name() const { return "algorithm_recovery"; }
    std::string AlgorithmRecoveryPlugin::get_version() const { return "0.1"; }
    std::string AlgorithmRecoveryPlugin::get_description() const { return "Native recovery of stateful memory and algorithm graphs."; }
    std::set<std::string> AlgorithmRecoveryPlugin::get_dependencies() const { return {"sequential_symbolic_execution", "dataflow", "module_identification", "bitorder_propagation"}; }
}
