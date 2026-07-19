export type DetectionStatus = 'authorized' | 'unauthorized' | 'unknown'
export type VehicleDues     = 'Clear' | 'Paid' | 'Remaining'

export interface User {
  username: string
  role:     string
}

export interface AuthData {
  access_token: string
  username:     string
  role:         string
}

export interface Vehicle {
  id:                 number
  vehicle_id_code:    string
  make:               string
  model:              string
  license_number:     string
  license_normalized: string
  color:              string
  owner_name:         string
  owner_cnic:         string
  dues:               VehicleDues
  status:             string
  is_authorized:      boolean
  image_filename:     string
}

export interface Detection {
  id:             number
  detected_plate: string
  matched_plate:  string
  vehicle_id:     number | null
  owner_name:     string
  status:         DetectionStatus
  confidence:     number
  image_path:     string | null
  detected_at:    string
}

// vehicle fields inside a live WS detection — subset of full DB row
export interface LiveVehicle {
  owner_name?:     string | null
  make?:           string | null
  model?:          string | null
  color?:          string | null
  dues?:           string | null
  license_number?: string | null
}

// payload inside the "detection" key from camera_worker.py
export interface LiveDetection {
  plate:      string
  status:     DetectionStatus
  ts:         string          // injected from frame.ts in LiveFeed
  yolo_conf:  number
  ocr_conf:   number
  match_type: string
  vehicle?:   LiveVehicle | null
}

export interface Stats {
  total_vehicles:         number
  authorized_vehicles:    number
  unauthorized_vehicles:  number
  total_detections_today: number
  authorized_today:       number
  unauthorized_today:     number
  total_detections_all:   number
}

export interface Paginated<T> {
  items:    T[]
  total:    number
  page:     number
  per_page: number
  pages:    number
}

export interface DetectionFilters {
  plate?:     string
  status?:    string
  date_from?: string
  date_to?:   string
}

export interface VehicleForm {
  vehicle_id_code: string
  make:            string
  model:           string
  license_number:  string
  color:           string
  owner_name:      string
  owner_cnic:      string
  dues:            VehicleDues
  status:          string
  image_filename:  string
}