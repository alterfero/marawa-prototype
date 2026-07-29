import { createContext, useContext } from "react";

import type { DatasetMaintenance } from "./api/types";

const availableMaintenance: DatasetMaintenance = {
  active: false,
  state: "available",
  message: null,
  job: null,
  target_dataset_version: null,
};

const DatasetMaintenanceContext = createContext<DatasetMaintenance>(availableMaintenance);

export function DatasetMaintenanceProvider({
  maintenance,
  children,
}: {
  maintenance: DatasetMaintenance | null;
  children: React.ReactNode;
}) {
  return (
    <DatasetMaintenanceContext.Provider value={maintenance ?? availableMaintenance}>
      {children}
    </DatasetMaintenanceContext.Provider>
  );
}

export function useDatasetMaintenance(): DatasetMaintenance {
  return useContext(DatasetMaintenanceContext);
}
