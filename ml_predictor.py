"""
Machine Learning Predictor para Trading
Usa Random Forest para predecir dirección del precio
"""
import numpy as np
import pickle
import os
import logging
from typing import Dict, Optional, List
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)

# Intentar importar scikit-learn
try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("⚠️ scikit-learn no disponible. ML desactivado.")


class MLPredictor:
    """Predictor basado en Machine Learning"""
    
    def __init__(self):
        self.model = None
        self.scaler = None
        self.is_trained = False
        self.feature_names = [
            'rsi', 'macd', 'macd_histogram', 'bb_position', 'bb_bandwidth',
            'trend_strength', 'price_change_1h', 'price_change_4h', 'volume_spike'
        ]
        
        self.training_data = []
        self.model_path = 'ml_model.pkl'
        self.scaler_path = 'ml_scaler.pkl'
        
        self.last_train_time = 0
        
        if not SKLEARN_AVAILABLE:
            logger.warning("⚠️ ML Predictor: scikit-learn no disponible")
            return
        
        # Inicializar modelo
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            min_samples_leaf=2,
            random_state=42,
            n_jobs=-1
        )
        
        self.scaler = StandardScaler()
        
        # Cargar modelo si existe
        self._load_model()
        
        logger.info("🤖 ML Predictor inicializado")
    
    def _load_model(self):
        """Cargar modelo pre-entrenado"""
        try:
            if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
                with open(self.model_path, 'rb') as f:
                    self.model = pickle.load(f)
                
                with open(self.scaler_path, 'rb') as f:
                    self.scaler = pickle.load(f)
                
                self.is_trained = True
                logger.info("✅ Modelo ML cargado desde disco")
        
        except Exception as e:
            logger.warning(f"⚠️ No se pudo cargar modelo: {e}")
    
    def _save_model(self):
        """Guardar modelo entrenado"""
        try:
            with open(self.model_path, 'wb') as f:
                pickle.dump(self.model, f)
            
            with open(self.scaler_path, 'wb') as f:
                pickle.dump(self.scaler, f)
            
            logger.info("💾 Modelo ML guardado")
        
        except Exception as e:
            logger.error(f"❌ Error guardando modelo: {e}")
    
    def extract_features(self, features_dict: Dict) -> Optional[np.ndarray]:
        """Extraer features del diccionario"""
        try:
            feature_values = []
            
            for name in self.feature_names:
                value = features_dict.get(name, 0)
                
                # Convertir valores categóricos
                if name == 'volume_spike':
                    value = 1 if value else 0
                elif isinstance(value, str):
                    value = 0
                
                feature_values.append(float(value))
            
            return np.array(feature_values).reshape(1, -1)
        
        except Exception as e:
            logger.debug(f"Error extrayendo features: {e}")
            return None
    
    def predict(self, features: Dict) -> Optional[Dict]:
        """
        Predecir dirección del precio
        
        Args:
            features: Diccionario con indicadores técnicos
        
        Returns:
            {'direction': 'LONG'|'SHORT'|'NEUTRAL', 'confidence': 0-1, 'probabilities': []}
        """
        if not SKLEARN_AVAILABLE or not Config.ML_ENABLED:
            return None
        
        if not self.is_trained:
            logger.debug("Modelo no entrenado aún")
            return None
        
        try:
            # Extraer features
            X = self.extract_features(features)
            if X is None:
                return None
            
            # Escalar
            X_scaled = self.scaler.transform(X)
            
            # Predecir
            prediction = self.model.predict(X_scaled)[0]
            probabilities = self.model.predict_proba(X_scaled)[0]
            
            # Interpretar
            # 0 = NEUTRAL, 1 = LONG, 2 = SHORT
            direction_map = {0: 'NEUTRAL', 1: 'LONG', 2: 'SHORT'}
            direction = direction_map.get(prediction, 'NEUTRAL')
            
            confidence = float(np.max(probabilities))
            
            return {
                'direction': direction,
                'confidence': round(confidence, 3),
                'probabilities': {
                    'NEUTRAL': round(float(probabilities[0]), 3),
                    'LONG': round(float(probabilities[1]), 3),
                    'SHORT': round(float(probabilities[2]), 3)
                }
            }
        
        except Exception as e:
            logger.debug(f"Error predicción: {e}")
            return None
    
    def add_training_sample(self, features: Dict, label: str):
        """
        Añadir muestra de entrenamiento
        
        Args:
            features: Features extraídas
            label: 'LONG', 'SHORT', 'NEUTRAL'
        """
        try:
            X = self.extract_features(features)
            if X is None:
                return
            
            # Mapear label
            label_map = {'NEUTRAL': 0, 'LONG': 1, 'SHORT': 2}
            y = label_map.get(label, 0)
            
            self.training_data.append({
                'features': X.flatten(),
                'label': y,
                'timestamp': datetime.now()
            })
            
            # Limitar tamaño
            if len(self.training_data) > 10000:
                self.training_data = self.training_data[-10000:]
        
        except Exception as e:
            logger.debug(f"Error añadiendo muestra: {e}")
    
    def train(self, min_samples: int = 100) -> bool:
        """
        Entrenar modelo
        
        Args:
            min_samples: Mínimo de muestras para entrenar
        
        Returns:
            True si entrenó con éxito
        """
        if not SKLEARN_AVAILABLE or not Config.ML_ENABLED:
            return False
        
        if len(self.training_data) < min_samples:
            logger.debug(f"Insuficientes muestras: {len(self.training_data)}/{min_samples}")
            return False
        
        try:
            logger.info(f"🤖 Entrenando modelo con {len(self.training_data)} muestras...")
            
            # Preparar datos
            X = np.array([sample['features'] for sample in self.training_data])
            y = np.array([sample['label'] for sample in self.training_data])
            
            # Verificar distribución
            unique, counts = np.unique(y, return_counts=True)
            logger.info(f"   Distribución: {dict(zip(['NEUTRAL', 'LONG', 'SHORT'], counts))}")
            
            # Escalar
            X_scaled = self.scaler.fit_transform(X)
            
            # Entrenar
            self.model.fit(X_scaled, y)
            
            # Evaluar
            score = self.model.score(X_scaled, y)
            logger.info(f"✅ Modelo entrenado | Accuracy: {score:.2%}")
            
            self.is_trained = True
            self.last_train_time = datetime.now().timestamp()
            
            # Guardar
            self._save_model()
            
            return True
        
        except Exception as e:
            logger.error(f"❌ Error entrenando: {e}")
            return False
    
    def should_retrain(self) -> bool:
        """Verificar si es hora de re-entrenar"""
        if not self.is_trained:
            return len(self.training_data) >= 100
        
        time_since_train = datetime.now().timestamp() - self.last_train_time
        
        return time_since_train > Config.ML_RETRAIN_INTERVAL
    
    def get_feature_importance(self) -> Dict:
        """Obtener importancia de features"""
        if not self.is_trained or not hasattr(self.model, 'feature_importances_'):
            return {}
        
        try:
            importances = self.model.feature_importances_
            
            importance_dict = {}
            for name, importance in zip(self.feature_names, importances):
                importance_dict[name] = round(float(importance), 4)
            
            # Ordenar
            sorted_importance = dict(sorted(importance_dict.items(), 
                                          key=lambda x: x[1], reverse=True))
            
            return sorted_importance
        
        except Exception as e:
            logger.debug(f"Error importance: {e}")
            return {}
    
    def get_stats(self) -> Dict:
        """Obtener estadísticas del modelo"""
        return {
            'trained': self.is_trained,
            'samples': len(self.training_data),
            'last_train': datetime.fromtimestamp(self.last_train_time).isoformat() if self.last_train_time > 0 else None,
            'sklearn_available': SKLEARN_AVAILABLE,
            'ml_enabled': Config.ML_ENABLED
        }
    
    def simulate_initial_training(self):
        """Generar datos sintéticos para entrenamiento inicial"""
        if not SKLEARN_AVAILABLE or self.is_trained:
            return
        
        logger.info("🎲 Generando datos sintéticos para entrenamiento inicial...")
        
        np.random.seed(42)
        
        for _ in range(500):
            # Generar features aleatorias con patrones
            rsi = np.random.uniform(20, 80)
            macd = np.random.uniform(-0.5, 0.5)
            macd_hist = np.random.uniform(-0.3, 0.3)
            bb_pos = np.random.uniform(10, 90)
            bb_bw = np.random.uniform(1, 10)
            trend_str = np.random.uniform(0, 50)
            price_1h = np.random.uniform(-5, 5)
            price_4h = np.random.uniform(-10, 10)
            vol_spike = np.random.choice([0, 1])
            
            features = {
                'rsi': rsi,
                'macd': macd,
                'macd_histogram': macd_hist,
                'bb_position': bb_pos,
                'bb_bandwidth': bb_bw,
                'trend_strength': trend_str,
                'price_change_1h': price_1h,
                'price_change_4h': price_4h,
                'volume_spike': vol_spike
            }
            
            # Determinar label basado en patrones
            if rsi < 30 and macd_hist > 0 and bb_pos < 30:
                label = 'LONG'
            elif rsi > 70 and macd_hist < 0 and bb_pos > 70:
                label = 'SHORT'
            else:
                label = 'NEUTRAL'
            
            self.add_training_sample(features, label)
        
        # Entrenar
        self.train(min_samples=100)
