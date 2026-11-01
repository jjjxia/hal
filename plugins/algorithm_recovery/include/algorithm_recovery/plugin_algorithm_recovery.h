#pragma once

#include "hal_core/plugin_system/plugin_interface_base.h"

namespace hal
{
    class PLUGIN_API AlgorithmRecoveryPlugin : public BasePluginInterface
    {
    public:
        std::string get_name() const override;
        std::string get_version() const override;
        std::string get_description() const override;
        std::set<std::string> get_dependencies() const override;
    };
}
